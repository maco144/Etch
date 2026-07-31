import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import PdfViewer from "../components/PdfViewer";
import SignatureField from "../components/SignatureField";
import SignaturePad from "../components/SignaturePad";
import TextFieldOverlay from "../components/TextFieldOverlay";
import {
  downloadDocument,
  EtchApiError,
  fetchDocumentChain,
  replaceDocument,
  stampEvent,
  type AssentEventPayload,
  type AssentReceipt,
  type FieldLocation,
} from "../lib/etch";
import {
  decrypt,
  encrypt,
  importKey,
  readKeyFromFragment,
  readWriteTokenFromFragment,
} from "../lib/crypto";
import { takePending } from "../lib/handoff";
import { newDocumentId, sha256, toArrayBuffer } from "../lib/hash";
import { pathFor } from "../lib/routing";
import { downloadBytes, downloadJson, flattenSignedPdf } from "../lib/pdf";
import type { RenderedPage, SignatureFieldValue, TextFieldValue } from "../lib/pdf";
import { signWithPasskey, type CapturedSignature } from "../lib/signatures";

type PlacementMode = "signature" | "text";

interface Stage {
  step: "placing" | "review" | "finalized";
  signer: { email: string; name: string };
}

const TEXT_FIELD_WIDTH_PT = 180;
const TEXT_FIELD_HEIGHT_PT = 22;
const TEXT_FIELD_FONT_PT = 12;

export default function Sign() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const pendingToken = params.get("p");
  const urlParams = useParams<{ documentId?: string }>();
  const recipientDocumentId = urlParams.documentId ?? null;
  // Recipient mode is any load that carries both a URL :documentId and a
  // #key= fragment. We decrypt client-side, sign, then re-encrypt back.
  const isRecipient = Boolean(recipientDocumentId && readKeyFromFragment());

  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [filename, setFilename] = useState<string>("document.pdf");
  const recipientKeyRef = useRef<CryptoKey | null>(null);
  const recipientWriteTokenRef = useRef<string | null>(null);
  const [tamperWarning, setTamperWarning] = useState<string | null>(null);

  const [documentId] = useState(() =>
    recipientDocumentId ?? newDocumentId(),
  );
  const originalHashRef = useRef<string | null>(null);
  const lastHashRef = useRef<string | null>(null);
  const eventIndexRef = useRef(0);
  const receiptRef = useRef<AssentReceipt | null>(null);

  const [activePage, setActivePage] = useState(1);
  const [pagesByNumber, setPagesByNumber] = useState<Map<number, RenderedPage>>(new Map());
  const [signatureFields, setSignatureFields] = useState<SignatureFieldValue[]>([]);
  const [signingFieldId, setSigningFieldId] = useState<string | null>(null);
  const credentialIdRef = useRef<string | null>(null);
  const [textFields, setTextFields] = useState<TextFieldValue[]>([]);
  const [placementMode, setPlacementMode] = useState<PlacementMode>("signature");
  const [autoFocusTextId, setAutoFocusTextId] = useState<string | null>(null);

  const [stage, setStage] = useState<Stage>({
    step: "placing",
    signer: { email: "", name: "" },
  });
  const [showPad, setShowPad] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyMsg, setBusyMsg] = useState<string | null>(null);
  const [finalReceipt, setFinalReceipt] = useState<AssentReceipt | null>(null);
  const [finalPdfUrl, setFinalPdfUrl] = useState<string | null>(null);

  // --- 1. Load the PDF -- two sources --------------------------------------
  //   a) self-sign: handoff from Home via sessionStorage token (?p=...)
  //   b) recipient: URL :documentId + #key= fragment → fetch + decrypt

  useEffect(() => {
    let cancelled = false;

    (async () => {
      if (isRecipient && recipientDocumentId) {
        const exported = readKeyFromFragment();
        if (!exported) {
          navigate(pathFor("home"));
          return;
        }
        // The write token lives in the same fragment as the key. It's not
        // required to decrypt + sign — only to push the signed copy back. If
        // it's missing (legacy link), we still render the sign UI but skip
        // the PUT at the end.
        recipientWriteTokenRef.current = readWriteTokenFromFragment();
        try {
          const [ciphertext, cryptoKey] = await Promise.all([
            downloadDocument(recipientDocumentId),
            importKey(exported),
          ]);
          if (cancelled) return;
          const plaintext = await decrypt(ciphertext, cryptoKey);
          if (cancelled) return;
          recipientKeyRef.current = cryptoKey;
          setBytes(plaintext);
          setFilename(`received-${recipientDocumentId.slice(0, 8)}.pdf`);
        } catch (err) {
          if (cancelled) return;
          const msg = err instanceof Error ? err.message : String(err);
          setError(
            `Couldn't open the shared document: ${msg}. ` +
              "The link may be broken, expired, or the #key fragment may be missing.",
          );
        }
        return;
      }

      if (!pendingToken) {
        navigate(pathFor("home"));
        return;
      }
      const pending = takePending(pendingToken);
      if (!pending) {
        navigate(pathFor("home"));
        return;
      }
      if (!cancelled) {
        setBytes(pending.bytes);
        setFilename(pending.filename);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pendingToken, navigate, isRecipient, recipientDocumentId]);

  // --- 2. Emit the `created` event as soon as we have the bytes -----------

  useEffect(() => {
    if (!bytes) return;
    let cancelled = false;
    (async () => {
      try {
        const hash = await sha256(bytes);
        if (cancelled) return;
        originalHashRef.current = hash;
        lastHashRef.current = hash;

        // Recipient mode: the sender stamped `uploaded` at index 0 with the
        // plaintext hash they held. We stitch `created` onto that chain and
        // verify the hash agrees with what we just decrypted — a mismatch
        // means the ciphertext was swapped between upload and retrieval.
        // Self-sign mode (or legacy recipient links with no uploaded event)
        // falls through to the standalone index=0 / parent=null case.
        let createdIndex = 0;
        let parentHash: string | null = null;
        if (isRecipient) {
          try {
            const chain = await fetchDocumentChain(documentId);
            if (cancelled) return;
            const uploaded = chain.events.find((e) => e.event_type === "uploaded");
            if (uploaded && uploaded.document_hash !== hash) {
              setTamperWarning(
                "The document we decrypted doesn't match the hash the sender " +
                  "registered on the Etch chain. The ciphertext may have been " +
                  "swapped after upload. Review carefully before signing.",
              );
            }
            if (chain.events.length > 0) {
              createdIndex = chain.event_count;
              parentHash = chain.events[chain.events.length - 1].document_hash;
            }
          } catch (err) {
            // 404 = no prior events (legacy sender). Transient errors fall
            // through to standalone mode rather than block signing — the
            // recipient can always sign locally even if the chain pre-fetch
            // fails.
            if (!(err instanceof EtchApiError && err.status === 404)) {
              console.warn("[etch] chain pre-fetch failed, continuing standalone", err);
            }
          }
        }

        const receipt = await stampEvent(buildEvent({
          documentId,
          eventType: "created",
          documentHash: hash,
          parentHash,
          eventIndex: createdIndex,
        }));
        if (cancelled) return;
        eventIndexRef.current = createdIndex + 1;
        receiptRef.current = receipt;
      } catch (err) {
        if (cancelled) return;
        setError(errorText(err, "Could not register the document with Etch."));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bytes, documentId, isRecipient]);

  // --- 3. Placement on the active page -----------------------------------

  const currentPage = pagesByNumber.get(activePage);

  const handlePlace = useCallback(
    (page: number, xPct: number, yPct: number) => {
      const target = pagesByNumber.get(page);
      if (!target) return;
      setActivePage(page);

      // Click point is the LEFT edge of the field — the field grows to the
      // right from where you clicked (vertically centered on your cursor).
      // This keeps placement predictable; centering-on-click made fields feel
      // like they "expanded" away from the clicked point.
      const clickXPt = xPct * target.widthPt;
      const clickYPt = yPct * target.heightPt;

      const place = (w: number, h: number) => ({
        x: Math.max(0, Math.min(target.widthPt - w, clickXPt)),
        y: Math.max(0, Math.min(target.heightPt - h, clickYPt - h / 2)),
        width: w,
        height: h,
      });

      if (placementMode === "text") {
        const id = crypto.randomUUID();
        const next: TextFieldValue = {
          id,
          page,
          ...place(TEXT_FIELD_WIDTH_PT, TEXT_FIELD_HEIGHT_PT),
          fontSize: TEXT_FIELD_FONT_PT,
          value: "",
        };
        setTextFields((prev) => [...prev, next]);
        setAutoFocusTextId(id);
        return;
      }

      // signature placement — always adds a new field, same as text fields.
      const next: SignatureFieldValue = {
        id: crypto.randomUUID(),
        page,
        ...place(200, 60),
        signed: false,
      };
      setSignatureFields((prev) => [...prev, next]);
      void emitFieldAdded(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pagesByNumber, placementMode],
  );

  const updateTextField = useCallback((next: TextFieldValue) => {
    setTextFields((prev) => prev.map((tf) => (tf.id === next.id ? next : tf)));
  }, []);
  const removeTextField = useCallback((id: string) => {
    setTextFields((prev) => prev.filter((tf) => tf.id !== id));
  }, []);
  const removeSignatureField = useCallback((id: string) => {
    setSignatureFields((prev) => prev.filter((f) => f.id !== id));
  }, []);
  const updateSignatureField = useCallback(
    (id: string, rect: { x: number; y: number; width: number; height: number }) => {
      setSignatureFields((prev) => prev.map((f) => (f.id === id ? { ...f, ...rect } : f)));
    },
    [],
  );

  const emitFieldAdded = async (loc: FieldLocation) => {
    if (!lastHashRef.current) return;
    try {
      await stampEvent(
        buildEvent({
          documentId,
          eventType: "field_added",
          documentHash: lastHashRef.current, // unchanged — we only recorded intent
          parentHash: lastHashRef.current,
          eventIndex: eventIndexRef.current,
          location: loc,
        }),
      );
      eventIndexRef.current += 1;
    } catch (err) {
      setError(errorText(err, "Could not register the field placement."));
    }
  };

  // --- 4. Signing --------------------------------------------------------

  const signerLabel = () =>
    stage.signer.email || stage.signer.name || "Anonymous signer";

  const startDrawnSignature = (fieldId: string) => {
    const target = signatureFields.find((f) => f.id === fieldId);
    if (!target || target.signed) return;
    setError(null);
    setSigningFieldId(fieldId);
    setShowPad(true);
  };

  const onDrawnSubmit = async (pngDataUrl: string, widthPx: number, heightPx: number) => {
    setShowPad(false);
    if (!signingFieldId) return;
    await signOneField(signingFieldId, { mode: "drawn", pngDataUrl, widthPx, heightPx });
  };

  const startPasskeySignature = async (fieldId: string) => {
    const target = signatureFields.find((f) => f.id === fieldId);
    if (!target || target.signed) return;
    if (!originalHashRef.current) return;
    setError(null);
    setSigningFieldId(fieldId);
    setBusyMsg("Waiting for your authenticator…");
    try {
      const sig = await signWithPasskey({
        documentHashHex: originalHashRef.current,
        userEmail: stage.signer.email || undefined,
        userName: stage.signer.name || undefined,
        existingCredentialIdB64: credentialIdRef.current ?? undefined,
      });
      credentialIdRef.current = sig.credentialId;
      await signOneField(fieldId, sig);
    } catch (err) {
      setBusyMsg(null);
      setSigningFieldId(null);
      setError(errorText(err, "Passkey signing failed."));
    }
  };

  const signOneField = async (fieldId: string, sig: CapturedSignature) => {
    const target = signatureFields.find((f) => f.id === fieldId);
    if (!target || !lastHashRef.current) return;
    setBusyMsg("Stamping signature…");
    try {
      await stampEvent(
        buildEvent({
          documentId,
          eventType: "signed",
          documentHash: lastHashRef.current,
          parentHash: lastHashRef.current,
          eventIndex: eventIndexRef.current,
          location: target,
          signer: {
            method: sig.mode,
            credential_id: sig.mode === "webauthn" ? sig.credentialId : undefined,
            attestation:
              sig.mode === "webauthn" ? sig.signature ?? undefined : undefined,
            email: stage.signer.email || undefined,
            name: stage.signer.name || undefined,
          },
        }),
      );
      eventIndexRef.current += 1;

      setSignatureFields((prev) =>
        prev.map((f) =>
          f.id === fieldId
            ? { ...f, signed: true, signature: sig, signerLabel: signerLabel(), signedAt: new Date().toISOString() }
            : f,
        ),
      );
    } catch (err) {
      setError(errorText(err, "Could not stamp the signature."));
    } finally {
      setBusyMsg(null);
      setSigningFieldId(null);
    }
  };

  const finishAndPublish = async () => {
    if (!bytes || !originalHashRef.current || !lastHashRef.current) return;
    if (signatureFields.length === 0 || !signatureFields.every((f) => f.signed)) return;
    setBusyMsg("Finalizing PDF…");
    try {
      const flattened = await flattenSignedPdf({
        originalBytes: bytes,
        signatures: signatureFields.map((f) => ({
          location: { page: f.page, x: f.x, y: f.y, width: f.width, height: f.height },
          png: f.signature!.pngDataUrl,
        })),
        textFields,
        documentId,
        verifyUrl: `${window.location.origin}/verify/${documentId}`,
        signerLabel: signerLabel(),
      });

      const finalizedHash = await sha256(flattened);
      const finalizedReceipt = await stampEvent(
        buildEvent({
          documentId,
          eventType: "finalized",
          documentHash: finalizedHash,
          parentHash: lastHashRef.current,
          eventIndex: eventIndexRef.current,
        }),
      );
      eventIndexRef.current += 1;
      lastHashRef.current = finalizedHash;

      // Recipient flow: re-encrypt the flattened PDF with the same key the
      // sender generated and PUT it back at the same doc_id. The sender's
      // link still decrypts with their copy of the key.
      //
      // The PUT is authorized by the write_token the sender embedded in the
      // link fragment. Links minted before that change have no token — the
      // user can still sign locally, they just can't push the signed copy
      // back over the E2EE channel.
      if (isRecipient && recipientKeyRef.current && recipientDocumentId) {
        if (!recipientWriteTokenRef.current) {
          console.warn("[etch] no write_token in fragment; skipping encrypted return");
        } else {
          setBusyMsg("Returning signed copy…");
          try {
            const reEncrypted = await encrypt(flattened, recipientKeyRef.current);
            await replaceDocument(
              recipientDocumentId,
              toArrayBuffer(reEncrypted),
              recipientWriteTokenRef.current,
            );
          } catch (err) {
            // Don't block the user — local download still works; we just warn.
            const msg = err instanceof Error ? err.message : String(err);
            setError(
              `Signed locally, but couldn't return the encrypted copy to the sender: ${msg}.`,
            );
          }
        }
      }

      const blob = new Blob([toArrayBuffer(flattened)], { type: "application/pdf" });
      setFinalPdfUrl(URL.createObjectURL(blob));
      setFinalReceipt(finalizedReceipt);
      setStage((s) => ({ ...s, step: "finalized" }));
      setBusyMsg(null);

      // Auto-download the signed PDF so the user has it before leaving.
      downloadBytes(
        flattened,
        suggestedDownloadName(filename, finalizedReceipt.id),
      );
    } catch (err) {
      setBusyMsg(null);
      setError(errorText(err, "Finalizing failed."));
      setStage((s) => ({ ...s, step: "review" }));
    }
  };

  const downloadReceiptJson = () => {
    if (!finalReceipt) return;
    downloadJson(
      {
        receipt: finalReceipt,
        document_id: documentId,
        filename,
        signer: stage.signer,
        verify_url: `${window.location.origin}/verify/${finalReceipt.id}`,
      },
      `${finalReceipt.id}.receipt.json`,
    );
  };

  // --- Memoized field pixel positions ------------------------------------

  const signatureOverlaysForActive = useMemo(() => {
    if (!currentPage) return null;
    const onPage = signatureFields.filter((f) => f.page === activePage);
    if (!onPage.length) return null;
    return (
      <>
        {onPage.map((f) => (
          <SignatureField
            key={f.id}
            field={f}
            pageWidthPx={currentPage.widthPx}
            pageHeightPx={currentPage.heightPx}
            pageWidthPt={currentPage.widthPt}
            pageHeightPt={currentPage.heightPt}
            editable={stage.step === "placing"}
            onChange={(rect) => updateSignatureField(f.id, rect)}
            onSign={() => startDrawnSignature(f.id)}
            onRemove={() => removeSignatureField(f.id)}
          />
        ))}
      </>
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signatureFields, currentPage, activePage, stage.step]);

  const textOverlaysForActive = useMemo(() => {
    if (!currentPage) return null;
    const onPage = textFields.filter((tf) => tf.page === activePage);
    if (!onPage.length) return null;
    return (
      <>
        {onPage.map((tf) => (
          <TextFieldOverlay
            key={tf.id}
            field={tf}
            pageWidthPx={currentPage.widthPx}
            pageHeightPx={currentPage.heightPx}
            pageWidthPt={currentPage.widthPt}
            pageHeightPt={currentPage.heightPt}
            editable={stage.step === "placing"}
            autoFocus={tf.id === autoFocusTextId}
            onChange={updateTextField}
            onRemove={removeTextField}
          />
        ))}
      </>
    );
  }, [textFields, currentPage, activePage, stage.step, autoFocusTextId, updateTextField, removeTextField]);

  // --- Render ------------------------------------------------------------

  if (!bytes) {
    return (
      <div className="p-16 text-center text-text-dim">Loading document…</div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-8 grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-8">
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm text-text-dim truncate">
            <span className="font-medium text-text">{filename}</span>
          </div>
          <div className="text-xs font-mono text-text-muted">{documentId}</div>
        </div>
        <PdfViewer
          bytes={bytes}
          activePage={activePage}
          onPageChange={setActivePage}
          onDocumentReady={({ pages }) => setPagesByNumber(pages)}
          onPageClick={stage.step === "placing" ? handlePlace : undefined}
        >
          {/* Signature placeholder first so it sits UNDER any text fields in
             the UI — you can always still see a filled-in text value even if
             a signature box overlaps it. Flatten renders them in the same
             under/over order in the final PDF. */}
          {signatureOverlaysForActive}
          {textOverlaysForActive}
        </PdfViewer>
      </div>

      <aside className="space-y-5 lg:sticky lg:top-20 h-fit">
        <StepIndicator stage={stage.step} />

        {stage.step === "placing" && (
          <div className="card p-5 space-y-4">
            <div>
              <div className="text-sm font-medium mb-1">Your identity</div>
              <div className="text-xs text-text-muted mb-3">
                Shown on the verify page. Optional — leave blank to sign anonymously.
              </div>
              <label className="block text-xs text-text-dim mb-1">Name</label>
              <input
                type="text"
                value={stage.signer.name}
                onChange={(e) =>
                  setStage((s) => ({
                    ...s,
                    signer: { ...s.signer, name: e.target.value },
                  }))
                }
                className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm"
              />
              <label className="block text-xs text-text-dim mb-1 mt-3">Email</label>
              <input
                type="email"
                value={stage.signer.email}
                onChange={(e) =>
                  setStage((s) => ({
                    ...s,
                    signer: { ...s.signer, email: e.target.value },
                  }))
                }
                className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm"
              />
            </div>
            <hr className="border-border" />
            <div>
              <div className="text-sm font-medium mb-2">Place fields</div>
              <div className="grid grid-cols-2 gap-2 mb-2">
                <button
                  type="button"
                  onClick={() => setPlacementMode("signature")}
                  className={`btn-secondary justify-center text-xs ${
                    placementMode === "signature"
                      ? "ring-1 ring-accent bg-accent/10"
                      : ""
                  }`}
                >
                  Signature
                </button>
                <button
                  type="button"
                  onClick={() => setPlacementMode("text")}
                  className={`btn-secondary justify-center text-xs ${
                    placementMode === "text"
                      ? "ring-1 ring-accent bg-accent/10"
                      : ""
                  }`}
                >
                  + Text field
                </button>
              </div>
              <p className="text-xs text-text-muted">
                {placementMode === "signature"
                  ? "Click on the document where you want a signature. Add as many as you need."
                  : "Click where you want a text field (printed name, date, etc.). Add as many as you need."}
              </p>
              {(signatureFields.length > 0 || textFields.length > 0) && (
                <p className="text-xs text-text-muted mt-1">
                  {signatureFields.length} signature field{signatureFields.length === 1 ? "" : "s"},{" "}
                  {textFields.length} text field{textFields.length === 1 ? "" : "s"} on this document.
                </p>
              )}
            </div>
            <hr className="border-border" />
            <div>
              <div className="text-sm font-medium mb-2">Sign</div>
              {signatureFields.length === 0 ? (
                <p className="text-xs text-text-muted">
                  Place a signature field above, then sign it here.
                </p>
              ) : (
                <ul className="space-y-2">
                  {signatureFields.map((f) => (
                    <li key={f.id} className="flex items-center justify-between gap-2">
                      <span className="text-xs text-text-dim">Page {f.page}</span>
                      {f.signed ? (
                        <span className="chip-success">Signed</span>
                      ) : (
                        <span className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() => startDrawnSignature(f.id)}
                            disabled={!!busyMsg}
                            className="btn-secondary text-xs px-2.5 py-1 disabled:opacity-40"
                          >
                            Draw
                          </button>
                          <button
                            type="button"
                            onClick={() => startPasskeySignature(f.id)}
                            disabled={!!busyMsg}
                            className="btn-primary text-xs px-2.5 py-1 disabled:opacity-40"
                          >
                            Passkey
                          </button>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

        {stage.step === "placing" && (
          <button
            type="button"
            onClick={() => setStage((s) => ({ ...s, step: "review" }))}
            disabled={signatureFields.length === 0 || !signatureFields.every((f) => f.signed)}
            className="btn-primary w-full justify-center disabled:opacity-40"
          >
            Review & Finish
          </button>
        )}

        {stage.step === "review" && (
          <div className="card p-5 space-y-4">
            <div className="text-sm font-medium">Review before publishing</div>
            <p className="text-xs text-text-muted">
              Scroll the document to check every signature and text field.
              Nothing is published until you click Finish &amp; Publish.
            </p>
            <ul className="space-y-1.5">
              {signatureFields.map((f) => (
                <li key={f.id} className="flex items-center justify-between text-xs">
                  <span className="text-text-dim">Page {f.page}</span>
                  <span className="chip-success">
                    Signed{f.signerLabel ? ` — ${f.signerLabel}` : ""}
                  </span>
                </li>
              ))}
            </ul>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setStage((s) => ({ ...s, step: "placing" }))}
                disabled={!!busyMsg}
                className="btn-secondary justify-center disabled:opacity-40"
              >
                Back to edit
              </button>
              <button
                type="button"
                onClick={finishAndPublish}
                disabled={!!busyMsg}
                className="btn-primary justify-center disabled:opacity-40"
              >
                Finish &amp; Publish
              </button>
            </div>
          </div>
        )}

        {busyMsg && (
          <div className="card p-5 text-sm text-text-dim">
            <div className="flex items-center gap-2">
              <Spinner />
              <span>{busyMsg}</span>
            </div>
          </div>
        )}

        {stage.step === "finalized" && finalReceipt && (
          <div className="card p-5 space-y-3">
            <div className="chip-success">Signed</div>
            <div className="text-sm">
              Receipt{" "}
              <span className="font-mono text-xs">{finalReceipt.id}</span>
            </div>
            <div className="text-xs text-text-dim">
              Your signed PDF downloaded automatically. Share the verify link
              with anyone who needs to check it:
            </div>
            <div className="font-mono text-xs break-all bg-elevated p-2 rounded">
              {window.location.origin}/verify/{finalReceipt.id}
            </div>
            <div className="flex flex-col gap-2">
              {finalPdfUrl && (
                <a
                  href={finalPdfUrl}
                  download={suggestedDownloadName(filename, finalReceipt.id)}
                  className="btn-secondary justify-center"
                >
                  Re-download signed PDF
                </a>
              )}
              <button
                type="button"
                onClick={downloadReceiptJson}
                className="btn-secondary justify-center"
              >
                Download receipt JSON
              </button>
              <Link
                to={`/verify/${finalReceipt.id}`}
                className="btn-primary justify-center"
              >
                View verify page
              </Link>
            </div>
          </div>
        )}

        {tamperWarning && (
          <div className="card border-danger/60 bg-danger/10 p-4 text-sm text-danger space-y-1">
            <div className="font-semibold uppercase tracking-wide text-xs">
              Tamper check failed
            </div>
            <div>{tamperWarning}</div>
          </div>
        )}

        {error && (
          <div className="card border-danger/40 bg-danger/10 p-4 text-sm text-danger">
            {error}
          </div>
        )}

        {showPad && (
          <SignaturePad
            onSubmit={onDrawnSubmit}
            onCancel={() => setShowPad(false)}
          />
        )}
      </aside>
    </div>
  );
}

function StepIndicator({ stage }: { stage: Stage["step"] }) {
  const steps: { key: Stage["step"]; label: string }[] = [
    { key: "placing", label: "Place & sign" },
    { key: "review", label: "Review" },
    { key: "finalized", label: "Done" },
  ];
  const idx = steps.findIndex((s) => s.key === stage);
  return (
    <ol className="flex items-center gap-2 text-xs">
      {steps.map((s, i) => (
        <li key={s.key} className="flex items-center gap-2">
          <span
            className={`w-5 h-5 flex items-center justify-center rounded-full text-[10px] font-semibold ${
              i <= idx ? "bg-accent text-white" : "bg-elevated text-text-muted"
            }`}
          >
            {i + 1}
          </span>
          <span className={i <= idx ? "text-text" : "text-text-muted"}>
            {s.label}
          </span>
          {i < steps.length - 1 && (
            <span className="w-8 h-px bg-border" aria-hidden />
          )}
        </li>
      ))}
    </ol>
  );
}

function Spinner() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      className="animate-spin"
      aria-hidden
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path
        d="M22 12a10 10 0 0 0-10-10"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function buildEvent(args: {
  documentId: string;
  eventType: AssentEventPayload["event_type"];
  documentHash: string;
  parentHash: string | null;
  eventIndex: number;
  location?: FieldLocation;
  signer?: AssentEventPayload["signer"];
}): AssentEventPayload {
  return {
    kind: "assent.event",
    schema_version: 1,
    document_id: args.documentId,
    event_type: args.eventType,
    document_hash: args.documentHash,
    parent_hash: args.parentHash,
    event_index: args.eventIndex,
    location: args.location,
    signer: args.signer,
    timestamp: new Date().toISOString(),
    client_metadata: {
      user_agent: navigator.userAgent,
      platform: navigator.platform,
    },
  };
}

function errorText(err: unknown, fallback: string): string {
  if (err instanceof EtchApiError) return `${fallback} (${err.status}: ${err.message})`;
  if (err instanceof Error) return `${fallback} ${err.message}`;
  return fallback;
}

function suggestedDownloadName(original: string, receiptId: string): string {
  const base = original.replace(/\.pdf$/i, "");
  return `${base}-signed-${receiptId}.pdf`;
}
