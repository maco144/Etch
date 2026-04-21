import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import PdfViewer from "../components/PdfViewer";
import SignatureField from "../components/SignatureField";
import SignaturePad from "../components/SignaturePad";
import {
  EtchApiError,
  stampEvent,
  type AssentEventPayload,
  type AssentReceipt,
  type FieldLocation,
} from "../lib/etch";
import { takePending } from "../lib/handoff";
import { newDocumentId, sha256, toArrayBuffer } from "../lib/hash";
import { pathFor } from "../lib/routing";
import { downloadBytes, downloadJson, flattenSignedPdf } from "../lib/pdf";
import type { RenderedPage } from "../lib/pdf";
import { signWithPasskey, type CapturedSignature } from "../lib/signatures";

interface Stage {
  step: "placing" | "signing" | "stamping" | "finalized";
  signer: { email: string; name: string };
}

export default function Sign() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const pendingToken = params.get("p");

  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [filename, setFilename] = useState<string>("document.pdf");

  const [documentId] = useState(() => newDocumentId());
  const originalHashRef = useRef<string | null>(null);
  const lastHashRef = useRef<string | null>(null);
  const eventIndexRef = useRef(0);
  const receiptRef = useRef<AssentReceipt | null>(null);

  const [activePage, setActivePage] = useState(1);
  const [pagesByNumber, setPagesByNumber] = useState<Map<number, RenderedPage>>(new Map());
  const [field, setField] = useState<FieldLocation | null>(null);

  const [stage, setStage] = useState<Stage>({
    step: "placing",
    signer: { email: "", name: "" },
  });
  const [capturedSig, setCapturedSig] = useState<CapturedSignature | null>(null);
  const [showPad, setShowPad] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyMsg, setBusyMsg] = useState<string | null>(null);
  const [finalReceipt, setFinalReceipt] = useState<AssentReceipt | null>(null);
  const [finalPdfUrl, setFinalPdfUrl] = useState<string | null>(null);

  // --- 1. Load the PDF handed off from Home.tsx --------------------------

  useEffect(() => {
    if (!pendingToken) {
      navigate(pathFor("home"));
      return;
    }
    const pending = takePending(pendingToken);
    if (!pending) {
      navigate(pathFor("home"));
      return;
    }
    setBytes(pending.bytes);
    setFilename(pending.filename);
  }, [pendingToken, navigate]);

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
        const receipt = await stampEvent(buildEvent({
          documentId,
          eventType: "created",
          documentHash: hash,
          parentHash: null,
          eventIndex: 0,
        }));
        if (cancelled) return;
        eventIndexRef.current = 1;
        receiptRef.current = receipt;
      } catch (err) {
        if (cancelled) return;
        setError(errorText(err, "Could not register the document with Etch."));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bytes, documentId]);

  // --- 3. Placement on the active page -----------------------------------

  const currentPage = pagesByNumber.get(activePage);

  const handlePlace = useCallback(
    (page: number, xPct: number, yPct: number) => {
      const target = pagesByNumber.get(page);
      if (!target) return;
      setActivePage(page);
      const fieldWidthPt = 200;
      const fieldHeightPt = 60;
      const centerXPt = xPct * target.widthPt;
      const centerYPt = yPct * target.heightPt;
      const next: FieldLocation = {
        page,
        x: Math.max(0, Math.min(target.widthPt - fieldWidthPt, centerXPt - fieldWidthPt / 2)),
        y: Math.max(0, Math.min(target.heightPt - fieldHeightPt, centerYPt - fieldHeightPt / 2)),
        width: fieldWidthPt,
        height: fieldHeightPt,
      };
      setField(next);
      void emitFieldAdded(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pagesByNumber],
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

  const startDrawnSignature = () => {
    if (!field) {
      setError("Click on a page to place the signature field first.");
      return;
    }
    setError(null);
    setShowPad(true);
  };

  const onDrawnSubmit = async (pngDataUrl: string, widthPx: number, heightPx: number) => {
    setShowPad(false);
    setCapturedSig({ mode: "drawn", pngDataUrl, widthPx, heightPx });
    await finalizeSignature({ mode: "drawn", pngDataUrl, widthPx, heightPx });
  };

  const startPasskeySignature = async () => {
    if (!field) {
      setError("Click on a page to place the signature field first.");
      return;
    }
    if (!originalHashRef.current) return;
    setError(null);
    setBusyMsg("Waiting for your authenticator…");
    try {
      const sig = await signWithPasskey({
        documentHashHex: originalHashRef.current,
        userEmail: stage.signer.email || undefined,
        userName: stage.signer.name || undefined,
      });
      setCapturedSig(sig);
      await finalizeSignature(sig);
    } catch (err) {
      setBusyMsg(null);
      setError(errorText(err, "Passkey signing failed."));
    }
  };

  const finalizeSignature = async (sig: CapturedSignature) => {
    if (!bytes || !field || !originalHashRef.current || !lastHashRef.current) return;
    setStage((s) => ({ ...s, step: "signing" }));
    setBusyMsg("Stamping signature…");
    try {
      // Emit the `signed` event against the pre-flatten document hash.
      const signedReceipt = await stampEvent(
        buildEvent({
          documentId,
          eventType: "signed",
          documentHash: lastHashRef.current,
          parentHash: lastHashRef.current,
          eventIndex: eventIndexRef.current,
          location: field,
          signer: {
            method: sig.mode,
            credential_id: sig.mode === "webauthn" ? sig.credentialId : undefined,
            attestation:
              sig.mode === "webauthn"
                ? sig.signature ?? undefined
                : undefined,
            email: stage.signer.email || undefined,
            name: stage.signer.name || undefined,
          },
        }),
      );
      eventIndexRef.current += 1;

      // Flatten + add QR watermark. The resulting PDF is what the user downloads.
      setBusyMsg("Finalizing PDF…");
      const flattened = await flattenSignedPdf({
        originalBytes: bytes,
        signaturePng: sig.pngDataUrl,
        location: field,
        receiptId: signedReceipt.id,
        documentId,
        verifyUrl: `${window.location.origin}/verify/${signedReceipt.id}`,
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
      setStage((s) => ({ ...s, step: "placing" }));
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

  const fieldOverlayForActive = useMemo(() => {
    if (!field || !currentPage) return null;
    if (field.page !== activePage) return null;
    return (
      <SignatureField
        field={field}
        pageWidthPx={currentPage.widthPx}
        pageHeightPx={currentPage.heightPx}
        pageWidthPt={currentPage.widthPt}
        pageHeightPt={currentPage.heightPt}
        signaturePngUrl={capturedSig?.pngDataUrl ?? null}
        onClear={stage.step === "placing" ? () => setField(null) : undefined}
      />
    );
  }, [field, currentPage, activePage, capturedSig, stage.step]);

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
          {fieldOverlayForActive}
        </PdfViewer>
      </div>

      <aside className="space-y-5 lg:sticky lg:top-20 h-fit">
        <StepIndicator stage={stage.step} hasField={!!field} />

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
              <div className="text-sm font-medium mb-2">Sign</div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={startDrawnSignature}
                  disabled={!field}
                  className="btn-secondary justify-center disabled:opacity-40"
                >
                  Draw
                </button>
                <button
                  type="button"
                  onClick={startPasskeySignature}
                  disabled={!field}
                  className="btn-primary justify-center disabled:opacity-40"
                >
                  Passkey
                </button>
              </div>
              {!field && (
                <p className="text-xs text-text-muted mt-2">
                  Click anywhere on the document to place a signature field.
                </p>
              )}
            </div>
          </div>
        )}

        {(stage.step === "signing" || stage.step === "stamping") && (
          <div className="card p-5 text-sm text-text-dim">
            <div className="flex items-center gap-2">
              <Spinner />
              <span>{busyMsg ?? "Working…"}</span>
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

function StepIndicator({ stage, hasField }: { stage: Stage["step"]; hasField: boolean }) {
  const steps: { key: Stage["step"] | "placed"; label: string }[] = [
    { key: "placing", label: "Place field" },
    { key: "placed", label: "Sign" },
    { key: "finalized", label: "Done" },
  ];
  const idx = stage === "finalized" ? 2 : hasField ? 1 : 0;
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
