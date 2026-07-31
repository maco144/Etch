# Assent Resizable Fields + Multi-Signature Review/Finalize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make placed signature/text fields resizable and movable, and replace assent.to's auto-publish-on-sign with an explicit multi-field review/finalize step.

**Architecture:** `assent-app/` is a static React+TS SPA (`assent-app/src/routes/Sign.tsx` is the signing screen). Signature and text fields are absolutely-positioned div overlays on a PDF.js canvas, scaled from PDF points to page pixels. This plan (1) extracts the point↔pixel pointer-drag math both overlay types already duplicate into one hook and adds resize/move handles, and (2) turns the single hard-coded signature field into an array of independently-signable fields, with a new `"review"` stage gating the existing flatten/finalize logic behind an explicit "Finish & Publish" click.

**Tech Stack:** React 18, TypeScript (`strict: true`, `noUnusedLocals`, `noUnusedParameters`), Tailwind, pdf-lib, pdfjs-dist. No backend changes — `etch/assent_api.py`'s event-type validation has no cardinality constraint, so multiple `field_added`/`signed` events per document already fit the existing chain model.

**Design doc:** `docs/superpowers/specs/2026-07-31-assent-resize-multi-signature-design.md`

## Global Constraints

- No new npm dependencies. No test framework exists in `assent-app/` (`npm run lint` = `tsc --noEmit`, no vitest/jest) and the approved design keeps it that way — verification per task is `tsc --noEmit` (must stay clean under `strict`, `noUnusedLocals`, `noUnusedParameters`) plus a manual QA pass in the browser (`npm run dev`), not automated tests.
- No backend changes. `etch/assent_api.py` requires no modification.
- Follow existing Tailwind utility classes already defined in `assent-app/src/styles/index.css` (`card`, `btn-primary`, `btn-secondary`, `chip-success`, `chip-muted`) — don't invent new ad hoc component classes.
- Coordinates for all fields stay in PDF points on the wire (`FieldLocation`/event payloads); only display math converts to pixels, exactly as the existing code already does via `scaleX = pageWidthPx / pageWidthPt`.
- Corner-only resize handles (nw/ne/sw/se) — no edge/midpoint handles (out of scope per design doc).
- No routing/permission logic behind the new optional `label` field on signature fields — it's schema headroom for later multi-party work, not built or exposed via any input UI in this plan.

---

## Task 1: Multi-field signature data model, placement, and per-field signing

**Files:**
- Modify: `assent-app/src/lib/pdf.ts:63-67` (add `SignatureFieldValue` type next to `TextFieldValue`)
- Modify: `assent-app/src/components/SignatureField.tsx` (full rewrite — per-instance props instead of one nullable field)
- Modify: `assent-app/src/routes/Sign.tsx` (state model, placement, signing functions, sidebar)

**Interfaces:**
- Consumes: `FieldLocation`, `stampEvent`, `buildEvent`, `signWithPasskey` (from `lib/signatures.ts`, already accepts `existingCredentialIdB64`), `SignaturePad`, `chip-success`/`btn-primary`/`btn-secondary` CSS classes.
- Produces: `SignatureFieldValue` type (`lib/pdf.ts`), `signatureFields: SignatureFieldValue[]` state, `signOneField(fieldId, sig)`, `removeSignatureField(id)`, `startDrawnSignature(fieldId)`, `startPasskeySignature(fieldId)`, `credentialIdRef`. `SignatureField` component's new prop shape: `{ field: SignatureFieldValue; pageWidthPx; pageHeightPx; pageWidthPt; pageHeightPt; editable: boolean; onSign: () => void; onRemove: () => void }`. Task 3 adds an `onChange` prop on top of this — don't add it here.

- [ ] **Step 1: Add `SignatureFieldValue` to `lib/pdf.ts`**

Add directly below the existing `TextFieldValue` interface (`assent-app/src/lib/pdf.ts:63-67`):

```ts
export interface TextFieldValue extends FieldLocation {
  id: string;
  value: string;
  fontSize: number;
}

export interface SignatureFieldValue extends FieldLocation {
  id: string;
  label?: string; // optional display tag ("Signer 1") — no routing behind it, no input UI yet
  signed: boolean;
  signature?: CapturedSignature;
  signerLabel?: string;
  signedAt?: string;
}
```

Add the import at the top of the file: `import type { CapturedSignature } from "./signatures";`

- [ ] **Step 2: Rewrite `SignatureField.tsx` to be per-instance**

Replace the entire file:

```tsx
import type { SignatureFieldValue } from "../lib/pdf";

interface Props {
  field: SignatureFieldValue;
  pageWidthPx: number;
  pageHeightPx: number;
  pageWidthPt: number;
  pageHeightPt: number;
  editable: boolean;
  onSign: () => void;
  onRemove: () => void;
}

/**
 * Renders one placed signature field as an overlay on its PDF page.
 * Coordinates on the wire are in PDF points; we scale for display.
 */
export default function SignatureField({
  field,
  pageWidthPx,
  pageHeightPx,
  pageWidthPt,
  pageHeightPt,
  editable,
  onSign,
  onRemove,
}: Props) {
  const interactive = editable && !field.signed;

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;
  const left = field.x * scaleX;
  const top = field.y * scaleY;
  const width = field.width * scaleX;
  const height = field.height * scaleY;

  return (
    <div
      className={`absolute border-2 border-dashed rounded-sm ${
        field.signed ? "border-success bg-success/5" : "border-accent bg-accent/10"
      } ${interactive ? "pointer-events-auto cursor-pointer" : "pointer-events-none"}`}
      style={{ left, top, width, height }}
      onClick={
        interactive
          ? (e) => {
              e.stopPropagation();
              onSign();
            }
          : undefined
      }
    >
      {field.signature ? (
        <img
          src={field.signature.pngDataUrl}
          alt="signature"
          className="w-full h-full object-contain"
        />
      ) : (
        <div className="flex flex-col items-center justify-center h-full">
          <span className="text-xs font-medium text-accent">Sign here</span>
          <span className="text-[10px] text-text-muted">click to sign</span>
        </div>
      )}
      {editable && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="pointer-events-auto absolute -top-7 right-0 text-xs text-text-dim hover:text-danger"
        >
          remove field
        </button>
      )}
    </div>
  );
}
```

Note `editable` (not `interactive`) gates the remove button — a signed field can still be removed (and re-placed/re-signed) as long as the document overall is still editable. Only the click-to-sign trigger requires `!field.signed`.

- [ ] **Step 3: Replace the single-field state in `Sign.tsx`**

Replace (`assent-app/src/routes/Sign.tsx:68`):

```ts
const [field, setField] = useState<FieldLocation | null>(null);
```

with:

```ts
const [signatureFields, setSignatureFields] = useState<SignatureFieldValue[]>([]);
const [signingFieldId, setSigningFieldId] = useState<string | null>(null);
const credentialIdRef = useRef<string | null>(null);
```

Remove the now-unused `const [capturedSig, setCapturedSig] = useState<CapturedSignature | null>(null);` (`Sign.tsx:77`) — signature data now lives per-field on `SignatureFieldValue.signature` instead of one global slot.

Update the import at the top of `Sign.tsx` to pull in `SignatureFieldValue` alongside the existing `TextFieldValue`:

```ts
import type { RenderedPage, SignatureFieldValue, TextFieldValue } from "../lib/pdf";
```

- [ ] **Step 4: Placement adds a field instead of replacing it**

Replace the signature branch of `handlePlace` (`Sign.tsx:250-256`):

```ts
      // signature placement (default)
      const next: FieldLocation = {
        page,
        ...place(200, 60),
      };
      setField(next);
      void emitFieldAdded(next);
```

with:

```ts
      // signature placement — always adds a new field, same as text fields.
      const next: SignatureFieldValue = {
        id: crypto.randomUUID(),
        page,
        ...place(200, 60),
        signed: false,
      };
      setSignatureFields((prev) => [...prev, next]);
      void emitFieldAdded(next);
```

`emitFieldAdded` (`Sign.tsx:269-286`) needs no change — it already takes `loc: FieldLocation`, and `SignatureFieldValue` structurally satisfies that.

- [ ] **Step 5: Add `removeSignatureField`**

Add next to the existing `updateTextField`/`removeTextField` pair (`Sign.tsx:262-267`):

```ts
const removeSignatureField = useCallback((id: string) => {
  setSignatureFields((prev) => prev.filter((f) => f.id !== id));
}, []);
```

- [ ] **Step 6: Replace the signing functions with per-field versions**

Replace `startDrawnSignature` through the end of `finalizeSignature` (`Sign.tsx:293-430`) with:

```ts
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
```

This intentionally drops the flatten/finalize tail that used to live in `finalizeSignature` — that logic moves to Task 2's `finishAndPublish`, gated behind the new review step. Between Task 1 and Task 2, a document cannot be completed end-to-end; verify Task 1 via the chain endpoint (see Step 9), not by producing a finished PDF.

- [ ] **Step 7: Render every placed signature field, not just one**

Replace `fieldOverlayForActive` (`Sign.tsx:448-462`) with:

```ts
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
          onSign={() => startDrawnSignature(f.id)}
          onRemove={() => removeSignatureField(f.id)}
        />
      ))}
    </>
  );
}, [signatureFields, currentPage, activePage, stage.step]);
```

Update the JSX that renders it (`Sign.tsx:512-517`, the `{fieldOverlayForActive}` line) to `{signatureOverlaysForActive}` instead. Leave the surrounding comment and `{textOverlaysForActive}` as-is.

Clicking a field body defaults to Draw (lower friction, no browser permission prompt); Passkey stays a deliberate sidebar-only action (Step 8) since it triggers a WebAuthn prompt.

- [ ] **Step 8: Replace the sidebar's placement text and single Sign block**

In the `stage.step === "placing"` card (`Sign.tsx:524-625`):

Replace the placement helper text (`Sign.tsx:583-595`):

```tsx
              <p className="text-xs text-text-muted">
                {placementMode === "signature"
                  ? field
                    ? "Signature placed. Click the page to move it."
                    : "Click on the document where you want to sign."
                  : "Click where you want a text field (printed name, date, etc.). Add as many as you need."}
              </p>
              {textFields.length > 0 && (
                <p className="text-xs text-text-muted mt-1">
                  {textFields.length} text field
                  {textFields.length === 1 ? "" : "s"} on this document.
                </p>
              )}
```

with:

```tsx
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
```

Replace the whole "Sign" block (`Sign.tsx:597-623`, from `<hr className="border-border" />` through the closing `</div>` of the Sign section) with:

```tsx
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
```

- [ ] **Step 9: Fix the two remaining compile errors from removing `field`**

The busy-spinner condition (`Sign.tsx:627`) currently reads `(stage.step === "signing" || stage.step === "stamping")` — those states are no longer set anywhere. Replace with:

```tsx
{busyMsg && (
  <div className="card p-5 text-sm text-text-dim">
    <div className="flex items-center gap-2">
      <Spinner />
      <span>{busyMsg}</span>
    </div>
  </div>
)}
```

`StepIndicator` is called as `<StepIndicator stage={stage.step} hasField={!!field} />` (`Sign.tsx:522`) — `field` no longer exists. For now (full relabel happens in Task 2), just change the call site to `<StepIndicator stage={stage.step} hasField={signatureFields.length > 0} />` so the app compiles.

- [ ] **Step 10: Typecheck**

Run: `cd assent-app && npm run lint`
Expected: no errors. If `noUnusedLocals` flags anything (e.g. a leftover `FieldLocation` import if it became unused, or `onMove` prop remnants), remove it.

- [ ] **Step 11: Manual QA**

Run: `cd assent-app && npm run dev`, open the printed local URL, drop a test PDF in.

- Switch placement mode to Signature, click 3 different spots (including a second page if the PDF has one). Confirm 3 separate boxes appear (not one moving box), and the sidebar "Sign" list shows 3 rows.
- Click one field's body directly on the canvas — confirm it opens the Draw pad (not Passkey).
- Draw a signature and submit. Confirm that field shows the drawn image and a "Signed" chip; confirm the other two are still "Sign here" placeholders with Draw/Passkey buttons.
- Sign a second field via the sidebar's Passkey button (needs a platform authenticator or a virtual one in devtools). Confirm no second "create passkey" enrollment prompt appears — it should go straight to the assertion prompt, reusing the credential from the first passkey use elsewhere in the session if you test that path, or enroll once and confirm subsequent Passkey signs on other fields don't re-enroll.
- Click "remove field" on the third (still unsigned) field — confirm it disappears from both the canvas and the sidebar list.
- Open devtools Network tab, confirm `POST /v1/assent/stamp` fired once per `field_added` and once per `signed` — no `finalized` event yet (expected — that lands in Task 2).

- [ ] **Step 12: Commit**

```bash
git add assent-app/src/lib/pdf.ts assent-app/src/components/SignatureField.tsx assent-app/src/routes/Sign.tsx
git commit -m "$(cat <<'EOF'
feat(assent): support multiple independently-signed signature fields

Signature placement now adds fields to an array instead of replacing
a single one, and each field is signed independently. Finalizing the
whole document is intentionally not wired up yet — that's gated
behind the review step landing next.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Review stage + Finish & Publish

**Files:**
- Modify: `assent-app/src/lib/pdf.ts:69-126` (`FlattenArgs`/`flattenSignedPdf` — loop over signatures instead of one)
- Modify: `assent-app/src/routes/Sign.tsx` (stage machine, `finishAndPublish`, review UI, `StepIndicator`)

**Interfaces:**
- Consumes: Task 1's `signatureFields`, `signerLabel()`, `credentialIdRef` (untouched).
- Produces: `finishAndPublish()`, `Stage["step"]` narrowed to `"placing" | "review" | "finalized"`, `flattenSignedPdf`'s new `FlattenArgs.signatures: { location: FieldLocation; png: string }[]` (replacing `location`/`signaturePng`, `receiptId` dropped).

- [ ] **Step 1: Change `flattenSignedPdf` to accept multiple signatures**

Replace `FlattenArgs` (`assent-app/src/lib/pdf.ts:69-78`):

```ts
export interface FlattenArgs {
  originalBytes: Uint8Array;
  signatures: { location: FieldLocation; png: string }[];
  textFields: TextFieldValue[];
  documentId: string;
  verifyUrl: string;
  signerLabel: string;
}
```

(`receiptId` is dropped — with multiple `signed` events there's no single receipt to embed; the QR/footer now points at the document's own `doc_...` id, which `resolveVerify` in `lib/etch.ts` already resolves the same way a `rec_...` id does.)

Replace the top-of-function range check and the single-signature drawing block (`assent-app/src/lib/pdf.ts:85-126`, everything from `const pdfDoc = await PDFDocument.load(...)` through the signature `drawImage` call) with:

```ts
export async function flattenSignedPdf(args: FlattenArgs): Promise<Uint8Array> {
  const pdfDoc = await PDFDocument.load(args.originalBytes);
  const pages = pdfDoc.getPages();

  const font = await pdfDoc.embedFont(StandardFonts.Helvetica);

  // Text fields first, so signatures visually sit on top if they overlap.
  // Empty-value fields are skipped — a user who placed a field and typed
  // nothing clearly didn't want it baked in.
  for (const tf of args.textFields) {
    if (!tf.value.trim()) continue;
    if (tf.page < 1 || tf.page > pages.length) continue;
    const page = pages[tf.page - 1];
    const { height: pageHeightPt } = page.getSize();
    const baselineY = pageHeightPt - tf.y - tf.height + (tf.height - tf.fontSize) / 2;
    page.drawText(tf.value, {
      x: tf.x + 2,
      y: baselineY,
      size: tf.fontSize,
      font,
      color: rgb(0.05, 0.05, 0.1),
    });
  }

  for (const sig of args.signatures) {
    if (sig.location.page < 1 || sig.location.page > pages.length) {
      throw new Error(`signature page ${sig.location.page} out of range`);
    }
    const sigPng = await pdfDoc.embedPng(sig.png);
    const targetPage = pages[sig.location.page - 1];
    const { height: pageHeight } = targetPage.getSize();
    const pdfY = pageHeight - sig.location.y - sig.location.height;
    targetPage.drawImage(sigPng, {
      x: sig.location.x,
      y: pdfY,
      width: sig.location.width,
      height: sig.location.height,
    });
  }
```

Everything from the "Audit watermark on the last page" comment onward (`assent-app/src/lib/pdf.ts:128-188` in the original file) is unchanged **except** the two `args.receiptId` uses become `args.documentId` and the `receipt:` keyword entry is dropped. For clarity, here is that entire trailing block as it should read after the edit — the QR generation and footer rectangle lines are copied verbatim from the existing file, only the `drawText`/`setSubject`/`setKeywords` calls near the bottom change:

```ts
  // Audit watermark on the last page.
  const lastPage = pages[pages.length - 1];
  const { width: lpW } = lastPage.getSize();
  const footerY = 36;

  const qrDataUrl = await QRCode.toDataURL(args.verifyUrl, {
    margin: 0,
    width: 128,
    color: { dark: "#0a0a0f", light: "#ffffff" },
  });
  const qrPng = await pdfDoc.embedPng(qrDataUrl);

  lastPage.drawRectangle({
    x: 24,
    y: footerY - 6,
    width: lpW - 48,
    height: 72,
    color: rgb(0.97, 0.97, 1),
    borderColor: rgb(0.85, 0.83, 0.95),
    borderWidth: 0.5,
  });

  lastPage.drawImage(qrPng, { x: 30, y: footerY, width: 60, height: 60 });
  lastPage.drawText("Verified via Etch Assent", {
    x: 100,
    y: footerY + 46,
    size: 10,
    font,
    color: rgb(0.1, 0.1, 0.15),
  });
  lastPage.drawText(`Document: ${args.documentId}`, {
    x: 100,
    y: footerY + 30,
    size: 8,
    font,
    color: rgb(0.3, 0.3, 0.4),
  });
  lastPage.drawText(`Signer: ${args.signerLabel}`, {
    x: 100,
    y: footerY + 16,
    size: 8,
    font,
    color: rgb(0.3, 0.3, 0.4),
  });
  lastPage.drawText(args.verifyUrl, {
    x: 100,
    y: footerY + 2,
    size: 7,
    font,
    color: rgb(0.35, 0.3, 0.55),
  });

  pdfDoc.setSubject(`etch-assent:${args.documentId}`);
  pdfDoc.setKeywords(["etch-assent", `document:${args.documentId}`]);
  pdfDoc.setProducer("Etch Assent");

  return pdfDoc.save({ useObjectStreams: false });
}
```

- [ ] **Step 2: Narrow the stage type and add `finishAndPublish`**

Change the `Stage` interface (`assent-app/src/routes/Sign.tsx:33-36`):

```ts
interface Stage {
  step: "placing" | "review" | "finalized";
  signer: { email: string; name: string };
}
```

Add `finishAndPublish` where `signOneField` was defined (Task 1, Step 6) — this is today's old `finalizeSignature` tail, now driven by the array:

```ts
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

    downloadBytes(flattened, suggestedDownloadName(filename, finalizedReceipt.id));
  } catch (err) {
    setBusyMsg(null);
    setError(errorText(err, "Finalizing failed."));
    setStage((s) => ({ ...s, step: "review" }));
  }
};
```

- [ ] **Step 3: Add the "Review & Finish" button and the review-stage sidebar block**

Immediately after the closing `</div>` of the `stage.step === "placing"` card (end of Task 1 Step 8's edits), add:

```tsx
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
```

`onPageClick={stage.step === "placing" ? handlePlace : undefined}` (`Sign.tsx:510`) already disables new placements once the stage leaves `"placing"` — no change needed there. Field overlays also already key their `editable` prop off `stage.step === "placing"` (Task 1 Step 7 / existing text field code), so switching to `"review"` already locks every field against further edits with no additional change.

- [ ] **Step 4: Relabel `StepIndicator` for the new 3-stage machine**

Replace the whole function (`assent-app/src/routes/Sign.tsx:703-731`):

```tsx
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
          <span className={i <= idx ? "text-text" : "text-text-muted"}>{s.label}</span>
          {i < steps.length - 1 && <span className="w-8 h-px bg-border" aria-hidden />}
        </li>
      ))}
    </ol>
  );
}
```

Update the call site (`Sign.tsx:522`, already touched once in Task 1 Step 9) to drop the now-gone `hasField` prop:

```tsx
<StepIndicator stage={stage.step} />
```

- [ ] **Step 5: Typecheck**

Run: `cd assent-app && npm run lint`
Expected: no errors.

- [ ] **Step 6: Manual QA**

Run: `cd assent-app && npm run dev`.

- Place 2 signature fields and 1 text field (fill in the text field). Confirm "Review & Finish" is disabled until both signature fields are signed.
- Sign both fields, confirm the button enables, click it.
- On the review screen: confirm the PDF viewer shows the signatures baked into the overlay (scroll/hover each page — overlays render per the page you're hovering, matching existing multi-page overlay behavior) and no further placement/signing is possible (clicking the page does nothing).
- Click "Back to edit" — confirm you're back in the placing stage and can still see both fields as "Signed", and that clicking "remove field" on one still works, allowing you to re-place and re-sign it.
- Return to review and click "Finish & Publish". Confirm exactly one `POST /v1/assent/stamp` fires with `event_type: "finalized"`, the PDF auto-downloads, and the downloaded PDF has both signature images and the typed text field baked in, plus a QR/footer on the last page linking to `/verify/{documentId}`.
- Visit that verify URL and confirm the full event chain renders: `created → field_added ×2 → signed ×2 → finalized` (plus one `field_added` for the text field's page click — text fields don't emit `field_added`, so it won't appear; that's existing behavior, unrelated to this change).

- [ ] **Step 7: Commit**

```bash
git add assent-app/src/lib/pdf.ts assent-app/src/routes/Sign.tsx
git commit -m "$(cat <<'EOF'
feat(assent): gate finalize behind an explicit review step

Signing every placed field no longer auto-publishes the document.
A new review stage shows exactly what will be produced and requires
an explicit Finish & Publish click, which now flattens every signed
field in one pass instead of assuming a single signature.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Resize + move for signature fields

**Files:**
- Create: `assent-app/src/hooks/useDraggableResizable.ts`
- Modify: `assent-app/src/components/SignatureField.tsx` (wire in the hook)
- Modify: `assent-app/src/routes/Sign.tsx` (add `updateSignatureField`, pass `onChange`)

**Interfaces:**
- Consumes: nothing from earlier tasks beyond `SignatureFieldValue`'s `x/y/width/height` shape.
- Produces: `useDraggableResizable(args): { bodyProps, handleProps }` — reused as-is by Task 4 for text fields. `Rect` and `Corner` types exported from the new hook file. `SignatureField` gains an `onChange: (rect: { x; y; width; height }) => void` prop.

- [ ] **Step 1: Create the shared pointer-drag/resize hook**

Create `assent-app/src/hooks/useDraggableResizable.ts`:

```ts
import { useCallback, useRef } from "react";

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type Corner = "nw" | "ne" | "sw" | "se";

export interface UseDraggableResizableArgs {
  rect: Rect;
  pageWidthPt: number;
  pageHeightPt: number;
  pageWidthPx: number;
  pageHeightPx: number;
  minWidthPt?: number;
  minHeightPt?: number;
  disabled?: boolean;
  onChange: (next: Rect) => void;
  onClick?: () => void;
}

const DEFAULT_MIN_WIDTH_PT = 40;
const DEFAULT_MIN_HEIGHT_PT = 20;
const CLICK_THRESHOLD_PX = 4;

interface DragState {
  mode: "move" | "resize";
  corner?: Corner;
  startClientX: number;
  startClientY: number;
  startRect: Rect;
  moved: boolean;
}

/**
 * Pointer-drag move + 4-corner resize for a field overlay, in PDF-point
 * space. Pointer deltas arrive in screen px; page pixel/point sizes convert
 * them to pt so callers only ever see/set positions in the wire format.
 */
export function useDraggableResizable({
  rect,
  pageWidthPt,
  pageHeightPt,
  pageWidthPx,
  pageHeightPx,
  minWidthPt = DEFAULT_MIN_WIDTH_PT,
  minHeightPt = DEFAULT_MIN_HEIGHT_PT,
  disabled = false,
  onChange,
  onClick,
}: UseDraggableResizableArgs) {
  const dragState = useRef<DragState | null>(null);

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;

  const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max);

  const beginMove = useCallback(
    (e: React.PointerEvent) => {
      if (disabled) return;
      // preventDefault on pointerdown suppresses the browser's compatibility
      // mousedown/mouseup/click events for this interaction (same reasoning
      // as SignaturePad.tsx's existing pointerdown handler) — without it, a
      // click-without-movement still emits a native "click" after pointerup
      // that would bubble past this element to the page's place-a-field
      // handler and add a duplicate field on top of the one just clicked.
      e.preventDefault();
      e.stopPropagation();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragState.current = {
        mode: "move",
        startClientX: e.clientX,
        startClientY: e.clientY,
        startRect: rect,
        moved: false,
      };
    },
    [disabled, rect],
  );

  const beginResize = useCallback(
    (corner: Corner) => (e: React.PointerEvent) => {
      if (disabled) return;
      e.preventDefault();
      e.stopPropagation();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragState.current = {
        mode: "resize",
        corner,
        startClientX: e.clientX,
        startClientY: e.clientY,
        startRect: rect,
        moved: false,
      };
    },
    [disabled, rect],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const drag = dragState.current;
      if (!drag) return;
      const deltaXpx = e.clientX - drag.startClientX;
      const deltaYpx = e.clientY - drag.startClientY;
      if (Math.abs(deltaXpx) > CLICK_THRESHOLD_PX || Math.abs(deltaYpx) > CLICK_THRESHOLD_PX) {
        drag.moved = true;
      }
      const deltaXpt = deltaXpx / scaleX;
      const deltaYpt = deltaYpx / scaleY;
      const { startRect } = drag;

      if (drag.mode === "move") {
        const x = clamp(startRect.x + deltaXpt, 0, pageWidthPt - startRect.width);
        const y = clamp(startRect.y + deltaYpt, 0, pageHeightPt - startRect.height);
        onChange({ ...startRect, x, y });
        return;
      }

      const left0 = startRect.x;
      const top0 = startRect.y;
      const right0 = startRect.x + startRect.width;
      const bottom0 = startRect.y + startRect.height;

      switch (drag.corner) {
        case "nw": {
          const left = clamp(left0 + deltaXpt, 0, right0 - minWidthPt);
          const top = clamp(top0 + deltaYpt, 0, bottom0 - minHeightPt);
          onChange({ x: left, y: top, width: right0 - left, height: bottom0 - top });
          break;
        }
        case "ne": {
          const right = clamp(right0 + deltaXpt, left0 + minWidthPt, pageWidthPt);
          const top = clamp(top0 + deltaYpt, 0, bottom0 - minHeightPt);
          onChange({ x: left0, y: top, width: right - left0, height: bottom0 - top });
          break;
        }
        case "sw": {
          const left = clamp(left0 + deltaXpt, 0, right0 - minWidthPt);
          const bottom = clamp(bottom0 + deltaYpt, top0 + minHeightPt, pageHeightPt);
          onChange({ x: left, y: top0, width: right0 - left, height: bottom - top0 });
          break;
        }
        case "se": {
          const right = clamp(right0 + deltaXpt, left0 + minWidthPt, pageWidthPt);
          const bottom = clamp(bottom0 + deltaYpt, top0 + minHeightPt, pageHeightPt);
          onChange({ x: left0, y: top0, width: right - left0, height: bottom - top0 });
          break;
        }
      }
    },
    [scaleX, scaleY, pageWidthPt, pageHeightPt, minWidthPt, minHeightPt, onChange],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      const drag = dragState.current;
      if (!drag) return;
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      if (drag.mode === "move" && !drag.moved) onClick?.();
      dragState.current = null;
    },
    [onClick],
  );

  return {
    bodyProps: disabled
      ? {}
      : { onPointerDown: beginMove, onPointerMove, onPointerUp, onPointerCancel: onPointerUp },
    handleProps: (corner: Corner) =>
      disabled
        ? {}
        : {
            onPointerDown: beginResize(corner),
            onPointerMove,
            onPointerUp,
            onPointerCancel: onPointerUp,
          },
  };
}
```

- [ ] **Step 2: Wire the hook into `SignatureField.tsx`**

Replace the full file (builds on Task 1's version):

```tsx
import { useDraggableResizable, type Corner } from "../hooks/useDraggableResizable";
import type { SignatureFieldValue } from "../lib/pdf";

interface Props {
  field: SignatureFieldValue;
  pageWidthPx: number;
  pageHeightPx: number;
  pageWidthPt: number;
  pageHeightPt: number;
  editable: boolean;
  onChange: (rect: { x: number; y: number; width: number; height: number }) => void;
  onSign: () => void;
  onRemove: () => void;
}

const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];
const CORNER_STYLE: Record<Corner, React.CSSProperties> = {
  nw: { left: -5, top: -5, cursor: "nwse-resize" },
  ne: { right: -5, top: -5, cursor: "nesw-resize" },
  sw: { left: -5, bottom: -5, cursor: "nesw-resize" },
  se: { right: -5, bottom: -5, cursor: "nwse-resize" },
};

/**
 * Renders one placed signature field as an overlay on its PDF page.
 * Coordinates on the wire are in PDF points; we scale for display.
 */
export default function SignatureField({
  field,
  pageWidthPx,
  pageHeightPx,
  pageWidthPt,
  pageHeightPt,
  editable,
  onChange,
  onSign,
  onRemove,
}: Props) {
  const interactive = editable && !field.signed;

  const { bodyProps, handleProps } = useDraggableResizable({
    rect: field,
    pageWidthPt,
    pageHeightPt,
    pageWidthPx,
    pageHeightPx,
    disabled: !interactive,
    onChange,
    onClick: interactive ? onSign : undefined,
  });

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;
  const left = field.x * scaleX;
  const top = field.y * scaleY;
  const width = field.width * scaleX;
  const height = field.height * scaleY;

  return (
    <div
      {...bodyProps}
      // Belt-and-suspenders alongside the hook's preventDefault: if a native
      // click still reaches this element (e.g. a real mouse click with no
      // pointerdown-driven drag at all), stop it from bubbling to the page's
      // place-a-field handler, same as TextFieldOverlay's `swallow` wrapper.
      onClick={(e) => e.stopPropagation()}
      className={`absolute border-2 border-dashed rounded-sm touch-none ${
        field.signed ? "border-success bg-success/5" : "border-accent bg-accent/10"
      } ${interactive ? "pointer-events-auto cursor-move" : "pointer-events-none"}`}
      style={{ left, top, width, height }}
    >
      {field.signature ? (
        <img
          src={field.signature.pngDataUrl}
          alt="signature"
          className="w-full h-full object-contain"
        />
      ) : (
        <div className="flex flex-col items-center justify-center h-full">
          <span className="text-xs font-medium text-accent">Sign here</span>
          <span className="text-[10px] text-text-muted">click to sign, drag to move</span>
        </div>
      )}
      {interactive &&
        CORNERS.map((c) => (
          <div
            key={c}
            {...handleProps(c)}
            className="absolute w-2.5 h-2.5 bg-accent border border-white rounded-sm pointer-events-auto touch-none"
            style={CORNER_STYLE[c]}
          />
        ))}
      {editable && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="pointer-events-auto absolute -top-7 right-0 text-xs text-text-dim hover:text-danger"
        >
          remove field
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add `updateSignatureField` and pass `onChange` down in `Sign.tsx`**

Add next to `removeSignatureField` (Task 1, Step 5):

```ts
const updateSignatureField = useCallback(
  (id: string, rect: { x: number; y: number; width: number; height: number }) => {
    setSignatureFields((prev) => prev.map((f) => (f.id === id ? { ...f, ...rect } : f)));
  },
  [],
);
```

In `signatureOverlaysForActive` (Task 1, Step 7), add the new prop to the `<SignatureField>` call:

```tsx
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
```

- [ ] **Step 4: Typecheck**

Run: `cd assent-app && npm run lint`
Expected: no errors.

- [ ] **Step 5: Manual QA**

Run: `cd assent-app && npm run dev`.

- Place a signature field. Confirm 4 small corner handles appear at its corners.
- Drag the se (bottom-right) handle inward — confirm the box shrinks and doesn't jump or snap; drag it past the field's top-left past the minimum size — confirm it stops shrinking around 40×20pt instead of collapsing or inverting.
- Drag the nw handle outward past the top edge of the page — confirm the box clamps at the page boundary instead of extending off-page.
- Drag the field's body (not a handle, not the remove button) to a new spot on the same page — confirm it moves smoothly and clamps at page edges.
- Click the field body without dragging (a real click) — confirm it still opens the Draw pad, i.e. the click-vs-drag threshold isn't misfiring as a drag.
- Sign the field, confirm the handles disappear (locked) and dragging/clicking it no longer does anything.

- [ ] **Step 6: Commit**

```bash
git add assent-app/src/hooks/useDraggableResizable.ts assent-app/src/components/SignatureField.tsx assent-app/src/routes/Sign.tsx
git commit -m "$(cat <<'EOF'
feat(assent): resize and drag-move placed signature fields

Adds corner-handle resize and body drag-to-move on unsigned signature
fields via a shared pointer-drag hook, fixing oversized fields that
overlapped surrounding page content. Fields lock once signed.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Resize + move for text fields

**Files:**
- Modify: `assent-app/src/components/TextFieldOverlay.tsx` (reuse Task 3's hook)

**Interfaces:**
- Consumes: `useDraggableResizable` from `assent-app/src/hooks/useDraggableResizable.ts` (Task 3).
- Produces: no new exports — `Sign.tsx`'s existing `onChange={updateTextField}` prop already accepts a full `TextFieldValue`, which is what the hook's `onChange` will construct, so no `Sign.tsx` changes are needed for this task.

- [ ] **Step 1: Add resize handles and a move grip to `TextFieldOverlay.tsx`**

Replace the full file:

```tsx
import { useEffect, useRef } from "react";
import type { TextFieldValue } from "../lib/pdf";
import { useDraggableResizable, type Corner } from "../hooks/useDraggableResizable";

interface Props {
  field: TextFieldValue;
  pageWidthPx: number;
  pageHeightPx: number;
  pageWidthPt: number;
  pageHeightPt: number;
  editable: boolean;
  autoFocus?: boolean;
  onChange: (next: TextFieldValue) => void;
  onRemove: (id: string) => void;
}

const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];
const CORNER_STYLE: Record<Corner, React.CSSProperties> = {
  nw: { left: -5, top: -5, cursor: "nwse-resize" },
  ne: { right: -5, top: -5, cursor: "nesw-resize" },
  sw: { left: -5, bottom: -5, cursor: "nesw-resize" },
  se: { right: -5, bottom: -5, cursor: "nwse-resize" },
};

/**
 * Renders an editable text field as an absolutely-positioned input overlaid
 * on the PDF page canvas. Coordinates on the wire are in PDF points; we scale
 * for display so the input tracks whatever render scale PdfViewer picked.
 */
export default function TextFieldOverlay({
  field,
  pageWidthPx,
  pageHeightPx,
  pageWidthPt,
  pageHeightPt,
  editable,
  autoFocus,
  onChange,
  onRemove,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus && inputRef.current) inputRef.current.focus();
  }, [autoFocus]);

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;

  const left = field.x * scaleX;
  const top = field.y * scaleY;
  const width = field.width * scaleX;
  const height = field.height * scaleY;
  const fontPx = field.fontSize * scaleY;

  const { bodyProps, handleProps } = useDraggableResizable({
    rect: field,
    pageWidthPt,
    pageHeightPt,
    pageWidthPx,
    pageHeightPx,
    disabled: !editable,
    onChange: (rect) => onChange({ ...field, ...rect }),
  });

  // Stop PDF click-to-place handler from firing when the user interacts with
  // the field itself (input, move grip, resize handles, or remove button).
  const swallow = (e: React.MouseEvent | React.KeyboardEvent) =>
    e.stopPropagation();

  return (
    <div
      className="absolute"
      style={{ left, top, width, height }}
      onMouseDown={swallow}
      onClick={swallow}
    >
      <input
        ref={inputRef}
        type="text"
        value={field.value}
        disabled={!editable}
        onChange={(e) => onChange({ ...field, value: e.target.value })}
        onKeyDown={swallow}
        placeholder="Type here…"
        className={`w-full h-full bg-white/95 border-2 ${
          editable ? "border-dashed border-accent" : "border-solid border-border/40"
        } rounded-sm px-1 text-black outline-none focus:bg-white focus:border-accent`}
        style={{
          fontSize: `${fontPx}px`,
          fontFamily: "Helvetica, Arial, sans-serif",
          lineHeight: 1.1,
        }}
      />
      {editable && (
        <div
          {...bodyProps}
          title="Drag to move"
          className="touch-none pointer-events-auto absolute -top-6 left-0 text-xs text-text-dim hover:text-accent bg-bg/80 px-1 rounded cursor-move select-none"
        >
          ⠿ move
        </div>
      )}
      {editable &&
        CORNERS.map((c) => (
          <div
            key={c}
            {...handleProps(c)}
            className="absolute w-2.5 h-2.5 bg-accent border border-white rounded-sm pointer-events-auto touch-none"
            style={CORNER_STYLE[c]}
          />
        ))}
      {editable && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove(field.id);
          }}
          title="Remove text field"
          className="absolute -top-6 right-0 text-xs text-text-dim hover:text-danger bg-bg/80 px-1 rounded"
        >
          remove
        </button>
      )}
    </div>
  );
}
```

The move grip is a separate small element (not the input itself) so dragging doesn't fight with clicking into the input to position the text cursor.

- [ ] **Step 2: Typecheck**

Run: `cd assent-app && npm run lint`
Expected: no errors.

- [ ] **Step 3: Manual QA**

Run: `cd assent-app && npm run dev`.

- Place a text field, type into it — confirm typing still works normally and clicking inside the input doesn't trigger a move.
- Drag the "⠿ move" grip above the field — confirm the field moves (and the input's typed value is preserved).
- Drag a corner handle — confirm resize works and the typed text stays visible/reflows within the new box size.
- Shrink it to the minimum size — confirm it clamps rather than collapsing.
- Finalize a document with a resized/moved text field (full flow from Task 1/2) and confirm the flattened PDF's text lands at the field's *final* position, not its original placement position.

- [ ] **Step 4: Commit**

```bash
git add assent-app/src/components/TextFieldOverlay.tsx
git commit -m "$(cat <<'EOF'
feat(assent): resize and drag-move placed text fields

Reuses the signature field's drag/resize hook so text fields get the
same corner-handle resize and move-grip drag, fixing the same
fixed-size-overlap problem for text fields.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
