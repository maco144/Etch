# Etch Assent — Resizable Fields + Multi-Signature Review/Finalize (Design)

**Status:** Approved
**Owner:** Alex
**Created:** 2026-07-31
**Location:** `~/etch/assent-app/` (this repo)

## Problem

Two issues in the assent.to signing flow (`assent-app/`), both rooted in the same V1
shortcut: the app was built for exactly one hard-coded 200×60pt signature field
that auto-finalizes the instant it's signed.

1. **Fixed-size fields.** `SignatureField.tsx` and `TextFieldOverlay.tsx` are
   absolutely-positioned divs with no resize handles. `Sign.tsx` always places a
   signature field at `place(200, 60)` — the box is often too big for the target
   area and overlaps surrounding content on the page.
2. **No review step.** `Sign.tsx` supports only a single signature field
   (`useState<FieldLocation | null>`). The moment it's signed, `finalizeSignature()`
   runs synchronously: stamps `signed`, flattens the PDF, stamps `finalized`,
   re-uploads/downloads — all in one call, with no way to place and fill in
   several signature areas before the document is officially done.

## Goals

- Signature and text fields can be resized (corner drag handles) and repositioned
  (drag body) after placement, clamped to a sane minimum and to page bounds.
- A document can have multiple signature fields. Each is signed independently.
- Nothing is published/finalized until the user explicitly confirms, after all
  placed signature fields are signed, via a review step.
- Data model supports tagging a field with a display label now, without wiring
  up actual multi-party routing (assigning fields to specific invited signers)
  — that stays out of scope, deferred to the existing V2 send-to-sign work.

## Non-goals

- Assigning/routing individual fields to different invited signers (auth/permission
  behind "who can fill this field") — V2 territory.
- Edge (top/bottom/left/right midpoint) resize handles — corners only.
- Per-field required/optional flags, font-size resize on text fields, undo/redo.

## A. Data model

`assent-app/src/lib/etch.ts` — `FieldLocation` (`page, x, y, width, height`, all in
PDF points) is unchanged and stays the wire format for `field_added`/`signed`
event `location` payloads.

`assent-app/src/routes/Sign.tsx` replaces the singular signature field state:

```ts
// was: const [field, setField] = useState<FieldLocation | null>(null);
const [signatureFields, setSignatureFields] = useState<SignatureFieldValue[]>([]);
```

New type (co-located with `Sign.tsx` or `lib/pdf.ts` next to `TextFieldValue`):

```ts
interface SignatureFieldValue extends FieldLocation {
  id: string;
  label?: string;                  // optional free-text tag ("Signer 1", "Witness").
                                    // Display only — no routing/permission logic.
                                    // Forward-compat for later multi-party work.
  signed: boolean;
  signature?: CapturedSignature;   // drawn PNG or webauthn result, once signed
  signerLabel?: string;            // name/email resolved at sign time (for receipt)
  signedAt?: string;
}
```

`TextFieldValue` (`lib/pdf.ts`) is unchanged structurally — it already models an
array of fields; only its overlay component gains resize/move (section B).

Placement behavior: clicking the page while `placementMode === "signature"` now
**appends** a new `SignatureFieldValue` to the array (previously it replaced the
single `field`). This matches how text-field placement already works — one
consistent click-to-add pattern instead of two different ones.

No backend changes. `etch/assent_api.py`'s `VALID_EVENT_TYPES` check has no
cardinality constraint on any event type, so emitting one `field_added` + one
`signed` event per signature field, and a single `finalized` event at the end,
fits the existing append-only chain model as-is.

## B. Resize + move interaction

`SignatureField.tsx` and `TextFieldOverlay.tsx` are already the same shape — an
absolutely-positioned div scaled from PDF points to page pixels via
`scaleX = pageWidthPx / pageWidthPt` / `scaleY`. Extract that shared pointer-math
into one hook, `useDraggableResizable(field, onChange, { minWidthPt, minHeightPt,
pageWidthPt, pageHeightPt })`, used by both components instead of duplicating it.

- **Move**: pointer-down anywhere on the field body (not a handle, not the
  remove button) starts a drag; position updates in PDF-point space, clamped to
  page bounds the same way `handlePlace`'s `place()` clamp already works today.
- **Resize**: 4 corner handles (nw/ne/sw/se) rendered as small squares at the
  box corners, visible only while the field is editable. Dragging a handle
  adjusts width/height (and x/y, for the two corners that move the top/left
  edge), clamped to a minimum of 40×20pt and to page bounds.
- A plain click (pointer-down + pointer-up with negligible movement — a
  movement threshold distinguishes this from a drag) on an **unsigned**
  signature field's body triggers the sign action for that field (section C).
- Once a signature field is **signed**, it locks: no move, resize, remove, or
  re-trigger of signing. Same convention text fields already use — `editable`
  gates rendering of handles/remove button — just keyed off the field's own
  `signed` flag instead of the global stage.
- Text fields keep their current lock condition (tied to `stage.step ===
  "placing"`, not per-field, since they have no signed/unsigned state of their
  own) but gain the same resize/move handles.

## C. Multi-field signing + review/finalize flow

`Sign.tsx` stage machine becomes `"placing" → "review" → "finalized"`. The old
`"signing"`/`"stamping"` sub-states are replaced by a per-field busy indicator
(signing one field no longer blocks the whole UI).

**Placing stage** — placement and signing both happen here:

- Sidebar lists every placed signature field (page #, label, status chip:
  "Unsigned" / "Signed ✓"), each with Draw/Passkey actions — equivalent to
  clicking the field body on canvas. Any order.
- `signOneField(fieldId, capturedSig)` replaces the signature-stamping half of
  today's `finalizeSignature()`: stamps a `signed` event (location + signer,
  same payload as today) for that field, stores the captured signature on the
  field entry, sets `signed: true`. No flatten, no `finalized` event yet.
- Text fields behave exactly as today — freely editable, never required.
- "Review & Finish" enables once ≥1 signature field exists and every placed
  signature field has `signed: true`.

**Review stage:**

- Reuses `PdfViewer` + the same overlay components in fully-locked mode, so it
  renders exactly what will be produced (signature images + typed text baked
  into the overlay view across all pages), not a blank re-check.
- Sidebar summary: each signature field's label/signer/timestamp, a "Back to
  edit" button (returns to `placing`, unlocking every field — including
  already-signed ones, so a bad signature can be redone before publishing), and
  "Finish & Publish".
- `finishAndPublish()` is today's tail end of `finalizeSignature()`: one
  `flattenSignedPdf` call over **all** signed fields + text fields, one
  `finalized` event, re-upload (recipient flow) / auto-download.

Going back to edit is non-destructive to the chain — it's an append-only log,
so earlier `signed` events for a field the user redoes simply remain as
history; only the final `finalized` event (and the document hash it points to)
represents the published state.

## D. Chain events & flatten changes

Example sequence for 2 signature fields + 1 text field:

```
created → field_added (sig1) → field_added (sig2) → signed (sig1) → signed (sig2) → finalized
```

`field_added`/`signed` order follows whatever order the user actually places/
signs fields in — not fixed.

`flattenSignedPdf` (`assent-app/src/lib/pdf.ts`) changes from a single
`location`/`signaturePng` pair to a loop over signed fields:

```ts
interface FlattenArgs {
  originalBytes: Uint8Array;
  signatures: { location: FieldLocation; png: string }[];  // was: location + signaturePng
  textFields: TextFieldValue[];
  receiptId: string;
  documentId: string;
  verifyUrl: string;
  signerLabel: string;
}
```

The text-field loop, QR footer, and PDF metadata stamping are unchanged — the
function already loops over `textFields`; signatures get the same treatment.
`signerLabel` on the footer becomes a de-duplicated join of each signed field's
`signerLabel` (today's single-signer case collapses to the same string, so this
is a no-op in practice until multi-party signing exists).

## UI changes summary

- `StepIndicator` gains a step: "Place & sign" → "Review" → "Done" (was "Place
  field" → "Sign" → "Done").
- `SignatureField.tsx` → rendered per-entry (like `TextFieldOverlay` already
  is), gains resize handles, drag-move, per-field status/remove, and the
  click-to-sign trigger.
- New sidebar list of signature fields with status chips, replacing the single
  "Place a signature field above first…" messaging.
- New review-stage summary panel + "Back to edit" / "Finish & Publish" buttons.

## Testing

No frontend test framework exists in `assent-app/` today (`npm run lint` is
`tsc --noEmit`; no vitest/jest). Verification is manual, same as the existing
convention:

- `tsc --noEmit` passes.
- Manual QA in browser (`npm run dev`): place 1 field, resize it smaller,
  confirm it doesn't overlap; place 3 signature fields across 2 pages + 2 text
  fields; sign fields out of order; verify "Review & Finish" stays disabled
  until all are signed; back-to-edit and redo a signature; finish & publish;
  confirm the downloaded PDF has all signatures/text in the right places and
  the `/verify/:id` chain shows the full expected event sequence.
- Recipient (E2EE) flow smoke-tested the same way, since `finalizeSignature`'s
  re-encrypt-and-PUT tail is reused unchanged in `finishAndPublish`.
