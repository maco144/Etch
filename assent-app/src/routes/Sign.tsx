import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import PdfViewer from "../components/PdfViewer";
import SignatureField from "../components/SignatureField";
import SignaturePad from "../components/SignaturePad";
import StampFieldOverlay from "../components/StampFieldOverlay";
import TextFieldOverlay from "../components/TextFieldOverlay";
import CheckboxFieldOverlay from "../components/CheckboxFieldOverlay";
import { detectFields, type DetectedField } from "../lib/detectFields";
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
import {
  downloadBytes,
  downloadJson,
  fitTextBox,
  flattenSignedPdf,
  type CheckboxFieldValue,
  type PdfDocProxy,
  type RenderedPage,
  type SignatureFieldValue,
  type TextFieldValue,
} from "../lib/pdf";
import { signWithPasskey, type CapturedSignature } from "../lib/signatures";

type PlacementMode = "signature" | "text" | "checkbox";

const CHECKBOX_SIZE_PT = 12;

interface DetectionSummary {
  form: number;
  guessed: number;
}

interface Stage {
  step: "placing" | "review" | "finalized";
  signer: { email: string; name: string };
}

const TEXT_FIELD_FONT_PT = 12;

const DEFAULT_STAMP_WIDTH_PT = 200;
const DEFAULT_STAMP_HEIGHT_PT = 68;
const DEFAULT_STAMP_MARGIN_PT = 24;

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
  const createdArgsRef = useRef<{ index: number; parentHash: string | null } | null>(null);
  const createdAttemptRef = useRef<Promise<boolean> | null>(null);

  const [activePage, setActivePage] = useState(1);
  const [pagesByNumber, setPagesByNumber] = useState<Map<number, RenderedPage>>(new Map());
  const [signatureFields, setSignatureFields] = useState<SignatureFieldValue[]>([]);
  const [signingFieldId, setSigningFieldId] = useState<string | null>(null);
  const credentialIdRef = useRef<string | null>(null);
  const [textFields, setTextFields] = useState<TextFieldValue[]>([]);
  const [checkboxes, setCheckboxes] = useState<CheckboxFieldValue[]>([]);
  const [detection, setDetection] = useState<DetectionSummary | null>(null);
  const detectedForRef = useRef<PdfDocProxy | null>(null);
  const [stampEnabled, setStampEnabled] = useState(true);
  const [stampField, setStampField] = useState<FieldLocation | null>(null);
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

        if (cancelled) return;
        createdArgsRef.current = { index: createdIndex, parentHash };
        await ensureCreated();
      } catch (err) {
        if (cancelled) return;
        setError(errorText(err, "Could not register the document with Etch."));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bytes, documentId, isRecipient]);

  /**
   * Stamp `created` if it hasn't landed yet. Every later event chains onto
   * it, so if the first attempt failed (rate limit, network blip on load)
   * each later event would be refused too and the session would be silently
   * dead. Instead, anything that writes to the chain calls this first and
   * the document recovers as soon as the server accepts it. Concurrent
   * callers share one attempt.
   */
  const ensureCreated = (): Promise<boolean> => {
    if (receiptRef.current) return Promise.resolve(true);
    if (createdAttemptRef.current) return createdAttemptRef.current;
    const args = createdArgsRef.current;
    const hash = originalHashRef.current;
    if (!args || !hash) return Promise.resolve(false);
    const attempt = (async () => {
      try {
        const receipt = await stampEvent(
          buildEvent({
            documentId,
            eventType: "created",
            documentHash: hash,
            parentHash: args.parentHash,
            eventIndex: args.index,
          }),
        );
        eventIndexRef.current = args.index + 1;
        receiptRef.current = receipt;
        setError(null);
        return true;
      } catch (err) {
        setError(errorText(err, "Could not register the document with Etch."));
        return false;
      } finally {
        createdAttemptRef.current = null;
      }
    })();
    createdAttemptRef.current = attempt;
    return attempt;
  };

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
        const box = fitTextBox(
          "",
          TEXT_FIELD_FONT_PT,
          target.widthPx / target.widthPt,
          target.heightPx / target.heightPt,
        );
        const next: TextFieldValue = {
          id,
          page,
          ...place(box.width, box.height),
          fontSize: TEXT_FIELD_FONT_PT,
          value: "",
          autoFit: true,
        };
        setTextFields((prev) => [...prev, next]);
        setAutoFocusTextId(id);
        return;
      }

      if (placementMode === "checkbox") {
        const next: CheckboxFieldValue = {
          id: crypto.randomUUID(),
          page,
          x: Math.max(0, Math.min(target.widthPt - CHECKBOX_SIZE_PT, clickXPt - CHECKBOX_SIZE_PT / 2)),
          y: Math.max(0, Math.min(target.heightPt - CHECKBOX_SIZE_PT, clickYPt - CHECKBOX_SIZE_PT / 2)),
          width: CHECKBOX_SIZE_PT,
          height: CHECKBOX_SIZE_PT,
          checked: true,
        };
        setCheckboxes((prev) => [...prev, next]);
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
    setTextFields((prev) =>
      prev.map((tf) => {
        // Typing over an auto-filled date makes it the signer's own value.
        if (tf.id === next.id) return next.value !== tf.value ? { ...next, prefilled: false } : next;
        // Widgets of one AcroForm field — or one running header detected on
        // every page — are one value shown in several places.
        const linked =
          (next.acroName && tf.acroName === next.acroName) || (next.group && tf.group === next.group);
        if (linked && tf.value !== next.value) {
          if (!tf.autoFit) return { ...tf, value: next.value };
          const page = pagesByNumber.get(tf.page);
          if (!page) return { ...tf, value: next.value };
          const { width } = fitTextBox(next.value, tf.fontSize, page.widthPx / page.widthPt, page.heightPx / page.heightPt, tf.emptyWidth);
          return { ...tf, value: next.value, width: Math.min(width, page.widthPt - tf.x) };
        }
        return tf;
      }),
    );
  }, [pagesByNumber]);
  const toggleCheckbox = useCallback((id: string) => {
    setCheckboxes((prev) => {
      const hit = prev.find((c) => c.id === id);
      if (!hit) return prev;
      const radio = hit.radioValue !== undefined;
      const checked = radio ? true : !hit.checked;
      return prev.map((c) => {
        if (c.id === id) return { ...c, checked };
        if (!hit.acroName || c.acroName !== hit.acroName) return c;
        // Radio: one option per group. Checkbox: kids of one field move together.
        if (radio) return c.radioValue !== undefined ? { ...c, checked: c.radioValue === hit.radioValue } : c;
        return c.radioValue === undefined ? { ...c, checked } : c;
      });
    });
  }, []);
  const updateCheckbox = useCallback((next: CheckboxFieldValue) => {
    setCheckboxes((prev) => prev.map((c) => (c.id === next.id ? next : c)));
  }, []);
  const removeCheckbox = useCallback((id: string) => {
    setCheckboxes((prev) => prev.filter((c) => c.id !== id));
  }, []);

  // --- 3b. Field detection ----------------------------------------------
  //   Runs once per loaded document, in the browser. Detected fields are
  //   ordinary fields the signer can edit or remove; detected signature
  //   fields stay suggestions until signed (see SignatureFieldValue.suggested).

  const runDetection = useCallback(
    async (doc: PdfDocProxy, pages: Map<number, RenderedPage>) => {
      if (detectedForRef.current === doc) return;
      detectedForRef.current = doc;
      let found: DetectedField[];
      try {
        found = (await detectFields(doc)).fields;
      } catch (err) {
        console.warn("[assent] field detection failed", err);
        return;
      }
      if (detectedForRef.current !== doc) return;
      const today = new Date().toLocaleDateString();
      const texts: TextFieldValue[] = [];
      const boxes: CheckboxFieldValue[] = [];
      const sigs: SignatureFieldValue[] = [];
      for (const f of found) {
        const loc = { page: f.page, x: f.x, y: f.y, width: f.width, height: f.height };
        if (f.kind === "text") {
          const prefilled = !f.value && f.hint === "date";
          const value = f.value || (prefilled ? today : "");
          const guessed = f.source === "heuristic";
          const text: TextFieldValue = {
            id: crypto.randomUUID(),
            ...loc,
            fontSize: f.fontSize,
            value,
            prefilled: prefilled || undefined,
            detected: true,
            label: f.label ? f.label.replace(/^\w/, (c) => c.toUpperCase()) : undefined,
            hint: f.hint,
            acroName: f.acroName,
            group: f.group,
            // A guessed blank hugs what's typed into it, starting at the
            // length of the line it was found on. A form widget keeps the box
            // the form's author drew.
            autoFit: guessed,
            emptyWidth: guessed ? f.width : undefined,
          };
          const page = pages.get(f.page);
          if (guessed && value && page) {
            text.width = fitTextBox(value, f.fontSize, page.widthPx / page.widthPt, page.heightPx / page.heightPt, f.width).width;
          }
          texts.push(text);
        } else if (f.kind === "checkbox") {
          boxes.push({
            id: crypto.randomUUID(),
            ...loc,
            checked: f.checked,
            detected: true,
            label: f.label,
            acroName: f.acroName,
            radioValue: f.radioValue,
            printedChecked: f.printedChecked,
          });
        } else {
          sigs.push({ id: crypto.randomUUID(), ...loc, signed: false, suggested: true, acroName: f.acroName });
        }
      }
      setTextFields((prev) => [...prev, ...texts]);
      setCheckboxes((prev) => [...prev, ...boxes]);
      setSignatureFields((prev) => [...prev, ...sigs]);
      setDetection({
        form: found.filter((f) => f.source === "acroform").length,
        guessed: found.filter((f) => f.source === "heuristic").length,
      });
    },
    [],
  );

  /** Drop detected fields the signer never used. Anything filled in stays. */
  const clearUnusedSuggestions = () => {
    setTextFields((prev) => prev.filter((f) => !f.detected || f.value.trim()));
    setCheckboxes((prev) => prev.filter((c) => !c.detected || c.checked));
    setSignatureFields((prev) => prev.filter((f) => !f.suggested));
    setDetection(null);
  };

  /** Identity-panel edits flow into blanks detected as "name"/"email" — unless the signer typed something else there. */
  const setSignerField = (key: "name" | "email", next: string) => {
    const prevValue = stage.signer[key];
    setStage((s) => ({ ...s, signer: { ...s.signer, [key]: next } }));
    setTextFields((prev) =>
      prev.map((f) => {
        if (f.hint !== key || (f.value !== "" && f.value !== prevValue)) return f;
        const page = pagesByNumber.get(f.page);
        if (!f.autoFit || !page) return { ...f, value: next };
        const { width } = fitTextBox(next, f.fontSize, page.widthPx / page.widthPt, page.heightPx / page.heightPt, f.emptyWidth);
        return { ...f, value: next, width: Math.min(width, page.widthPt - f.x) };
      }),
    );
  };
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
  const updateStampField = useCallback(
    (rect: { x: number; y: number; width: number; height: number }) => {
      setStampField((prev) => (prev ? { ...prev, ...rect } : prev));
    },
    [],
  );

  const emitFieldAdded = async (loc: FieldLocation) => {
    if (!lastHashRef.current) return;
    if (!(await ensureCreated())) return;
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
      if (!(await ensureCreated())) return;
      if (target.suggested) {
        // Accepting a suggestion is placing it: same field_added a click
        // would have written, then the signature on top.
        await emitFieldAdded({ page: target.page, x: target.x, y: target.y, width: target.width, height: target.height });
      }
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
            ? {
                ...f,
                signed: true,
                suggested: false,
                signature: sig,
                signerLabel: signerLabel(),
                signedAt: new Date().toISOString(),
              }
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

  // What it takes to finish: every signature field that was placed has been
  // signed, and there is at least one thing to bake in. A signature is not
  // required — a filled-in text field (printed name, date, initials) is a
  // complete document on its own, and the chain, not the ink, is what proves
  // it. Empty text fields don't count; flattenSignedPdf skips them, so a
  // document with nothing but blank boxes would finalize as an unchanged copy.
  // An auto-filled date on the same row as a signature box belongs to that
  // signature: it goes into the PDF only if the signature was given. Without
  // this, every "Date:" beside an unused second-signer or staff line would be
  // stamped with today. Dates the signer typed, and standalone dates, stay.
  const filledTextFields = useMemo(
    () =>
      textFields.filter((f) => {
        if (!f.value.trim()) return false;
        if (!f.prefilled) return true;
        const bottom = f.y + f.height;
        const row = signatureFields.filter((s) => s.page === f.page && Math.abs(s.y + s.height - bottom) < 14);
        return row.length === 0 || row.some((s) => s.signed);
      }),
    [textFields, signatureFields],
  );
  const checkedBoxes = useMemo(() => checkboxes.filter((c) => c.checked), [checkboxes]);
  // Unsigned suggestions don't block — they're dropped at finalize.
  const unsignedCount = signatureFields.filter((f) => !f.signed && !f.suggested).length;
  const signedFields = signatureFields.filter((f) => f.signed);
  const canFinish =
    unsignedCount === 0 && (signedFields.length > 0 || filledTextFields.length > 0 || checkedBoxes.length > 0);

  const finishAndPublish = async () => {
    if (!bytes || !originalHashRef.current || !lastHashRef.current) return;
    if (!canFinish) return;
    setBusyMsg("Finalizing PDF…");
    try {
      if (!(await ensureCreated())) {
        setBusyMsg(null);
        return;
      }
      const flattened = await flattenSignedPdf({
        originalBytes: bytes,
        signatures: signedFields.map((f) => ({
          location: { page: f.page, x: f.x, y: f.y, width: f.width, height: f.height },
          png: f.signature!.pngDataUrl,
        })),
        textFields: filledTextFields,
        checkboxes,
        // Every AcroForm field still on the page is drawn by us, so its
        // original widget goes. A field the signer removed keeps its widget.
        acroNames: [
          ...new Set(
            [...textFields, ...checkboxes, ...signatureFields]
              .map((f) => f.acroName)
              .filter((n): n is string => !!n),
          ),
        ],
        documentId,
        verifyUrl: `${window.location.origin}/verify/${documentId}`,
        signerLabel: signerLabel(),
        stamp: stampEnabled ? stampField : null,
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
    // Past placing, an unsigned suggestion is just going to be dropped — don't show it.
    const onPage = signatureFields.filter(
      (f) => f.page === activePage && (stage.step === "placing" || !f.suggested),
    );
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

  const checkboxOverlaysForActive = useMemo(() => {
    if (!currentPage) return null;
    const onPage = checkboxes.filter((c) => c.page === activePage);
    if (!onPage.length) return null;
    return (
      <>
        {onPage.map((c) => (
          <CheckboxFieldOverlay
            key={c.id}
            field={c}
            pageWidthPx={currentPage.widthPx}
            pageHeightPx={currentPage.heightPx}
            pageWidthPt={currentPage.widthPt}
            pageHeightPt={currentPage.heightPt}
            editable={stage.step === "placing"}
            onToggle={toggleCheckbox}
            onChange={updateCheckbox}
            onRemove={removeCheckbox}
          />
        ))}
      </>
    );
  }, [checkboxes, currentPage, activePage, stage.step, toggleCheckbox, updateCheckbox, removeCheckbox]);

  const stampOverlayForActive = useMemo(() => {
    if (!stampEnabled || !stampField || !currentPage) return null;
    if (stampField.page !== activePage) return null;
    return (
      <StampFieldOverlay
        field={stampField}
        pageWidthPx={currentPage.widthPx}
        pageHeightPx={currentPage.heightPx}
        pageWidthPt={currentPage.widthPt}
        pageHeightPt={currentPage.heightPt}
        editable={stage.step === "placing"}
        verifyUrl={`${window.location.origin}/verify/${documentId}`}
        onChange={updateStampField}
        onRemove={() => setStampEnabled(false)}
      />
    );
  }, [stampEnabled, stampField, currentPage, activePage, stage.step, documentId, updateStampField]);

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
          onDocumentReady={({ numPages, pages, doc }) => {
            setPagesByNumber(pages);
            void runDetection(doc, pages);
            setStampField((prev) => {
              if (prev) return prev;
              const lastPage = pages.get(numPages);
              if (!lastPage) return prev;
              return {
                page: numPages,
                x: lastPage.widthPt - DEFAULT_STAMP_MARGIN_PT - DEFAULT_STAMP_WIDTH_PT,
                y: lastPage.heightPt - DEFAULT_STAMP_MARGIN_PT - DEFAULT_STAMP_HEIGHT_PT,
                width: DEFAULT_STAMP_WIDTH_PT,
                height: DEFAULT_STAMP_HEIGHT_PT,
              };
            });
          }}
          onPageClick={stage.step === "placing" ? handlePlace : undefined}
        >
          {/* Signature placeholder first so it sits UNDER any text fields in
             the UI — you can always still see a filled-in text value even if
             a signature box overlaps it. Flatten renders them in the same
             under/over order in the final PDF. */}
          {signatureOverlaysForActive}
          {textOverlaysForActive}
          {checkboxOverlaysForActive}
          {stampOverlayForActive}
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
                onChange={(e) => setSignerField("name", e.target.value)}
                className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm"
              />
              <label className="block text-xs text-text-dim mb-1 mt-3">Email</label>
              <input
                type="email"
                value={stage.signer.email}
                onChange={(e) => setSignerField("email", e.target.value)}
                className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm"
              />
            </div>
            <hr className="border-border" />
            <div>
              <div className="text-sm font-medium mb-2">Place fields</div>
              {detection && detection.form + detection.guessed > 0 && (
                <div className="mb-3 rounded border border-accent/30 bg-accent/5 px-3 py-2 text-xs text-text-dim">
                  {detection.form > 0 && (
                    <div>
                      Filled in from this PDF&apos;s form: {detection.form} field{detection.form === 1 ? "" : "s"}.
                    </div>
                  )}
                  {detection.guessed > 0 && (
                    <div>
                      Detected {detection.guessed} blank{detection.guessed === 1 ? "" : "s"} — check each one; empty
                      ones are left out.
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={clearUnusedSuggestions}
                    className="mt-1 text-accent hover:underline"
                  >
                    Clear unused suggestions
                  </button>
                </div>
              )}
              <div className="grid grid-cols-3 gap-2 mb-2">
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
                  + Text
                </button>
                <button
                  type="button"
                  onClick={() => setPlacementMode("checkbox")}
                  className={`btn-secondary justify-center text-xs ${
                    placementMode === "checkbox"
                      ? "ring-1 ring-accent bg-accent/10"
                      : ""
                  }`}
                >
                  + Check
                </button>
              </div>
              <p className="text-xs text-text-muted">
                {placementMode === "signature"
                  ? "Click on the document where you want a signature. Add as many as you need."
                  : placementMode === "text"
                    ? "Click where you want a text field (printed name, date, etc.). Add as many as you need."
                    : "Click where you want a check mark. Click a box to toggle it."}
              </p>
              {(signatureFields.length > 0 || textFields.length > 0 || checkboxes.length > 0) && (
                <p className="text-xs text-text-muted mt-1">
                  {signatureFields.length} signature field{signatureFields.length === 1 ? "" : "s"},{" "}
                  {textFields.length} text field{textFields.length === 1 ? "" : "s"}
                  {checkboxes.length > 0 && `, ${checkboxes.length} checkbox${checkboxes.length === 1 ? "" : "es"}`} on
                  this document.
                </p>
              )}
            </div>
            <hr className="border-border" />
            <div>
              <div className="text-sm font-medium mb-2">Sign</div>
              {signatureFields.length === 0 ? (
                <p className="text-xs text-text-muted">
                  Place a signature field above, then sign it here. Optional —
                  text fields or check marks alone are enough to finish.
                </p>
              ) : (
                <ul className="space-y-2">
                  {signatureFields.map((f) => (
                    <li key={f.id} className="flex items-center justify-between gap-2">
                      <span className="text-xs text-text-dim">
                        Page {f.page}
                        {f.suggested && <span className="text-text-muted"> · suggested</span>}
                      </span>
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
            <hr className="border-border" />
            <div>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={stampEnabled}
                  onChange={(e) => setStampEnabled(e.target.checked)}
                  className="accent-accent"
                />
                Include verification stamp
              </label>
              <p className="text-xs text-text-muted mt-1">
                A QR code linking to the verify page, stamped on the last
                page. Drag or resize it on the document, or turn it off here.
              </p>
            </div>
          </div>
        )}

        {stage.step === "placing" && (
          <button
            type="button"
            onClick={() => setStage((s) => ({ ...s, step: "review" }))}
            disabled={!canFinish}
            className="btn-primary w-full justify-center disabled:opacity-40"
          >
            Review & Finish
          </button>
        )}

        {stage.step === "placing" && !canFinish && (
          <p className="text-xs text-text-muted -mt-3 text-center">
            {unsignedCount > 0
              ? `Sign ${unsignedCount} remaining signature field${unsignedCount === 1 ? "" : "s"} to continue.`
              : "Place a signature, fill in a text field, or check a box to continue."}
          </p>
        )}

        {stage.step === "review" && (
          <div className="card p-5 space-y-4">
            <div className="text-sm font-medium">Review before publishing</div>
            <p className="text-xs text-text-muted">
              Scroll the document to check every signature and text field.
              Nothing is published until you click Finish &amp; Publish.
            </p>
            <ul className="space-y-1.5">
              {signedFields.map((f) => (
                <li key={f.id} className="flex items-center justify-between text-xs">
                  <span className="text-text-dim">Page {f.page}</span>
                  <span className="chip-success">
                    Signed{f.signerLabel ? ` — ${f.signerLabel}` : ""}
                  </span>
                </li>
              ))}
              {filledTextFields.map((f) => (
                <li key={f.id} className="flex items-center justify-between gap-3 text-xs">
                  <span className="text-text-dim shrink-0">Page {f.page}</span>
                  <span className="text-text-muted truncate">{f.value}</span>
                </li>
              ))}
              {checkedBoxes.map((c) => (
                <li key={c.id} className="flex items-center justify-between gap-3 text-xs">
                  <span className="text-text-dim shrink-0">Page {c.page}</span>
                  <span className="text-text-muted truncate">☑ {c.label ?? "Checked"}</span>
                </li>
              ))}
            </ul>
            {textFields.length > filledTextFields.length && (
              <p className="text-xs text-text-muted">
                {textFields.length - filledTextFields.length} empty text field
                {textFields.length - filledTextFields.length === 1 ? "" : "s"} will be
                left out of the finished PDF.
              </p>
            )}
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
          // A modal, not a sidebar card: a detected signature box can be on
          // page 8 of a long form, and the bottom of the sidebar would be
          // off-screen from there.
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            role="dialog"
            aria-modal="true"
            aria-label="Draw your signature"
            onKeyDown={(e) => e.key === "Escape" && setShowPad(false)}
          >
            <div className="w-full max-w-xl">
              <SignaturePad onSubmit={onDrawnSubmit} onCancel={() => setShowPad(false)} />
            </div>
          </div>
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
  if (err instanceof EtchApiError && err.status === 429) {
    return (
      "Etch is limiting how many documents your network can register right now. " +
      "Nothing you've placed is lost — try again in a few minutes and it will pick up where you left off."
    );
  }
  if (err instanceof EtchApiError) return `${fallback} (${err.status}: ${err.message})`;
  if (err instanceof Error) return `${fallback} ${err.message}`;
  return fallback;
}

function suggestedDownloadName(original: string, receiptId: string): string {
  const base = original.replace(/\.pdf$/i, "");
  return `${base}-signed-${receiptId}.pdf`;
}
