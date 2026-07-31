# Etch Assent — Customizable Verification Stamp (Design)

**Status:** Approved
**Owner:** Alex
**Created:** 2026-07-31
**Location:** `~/etch/assent-app/` (this repo)

## Problem

The verify-QR stamp `flattenSignedPdf` draws on the last page is fixed: fixed
size, fixed position (bottom-right, since the earlier fix in this session),
always on. That earlier fix shrank it from a 564pt full-width banner to a
200×68pt corner badge, which resolves the "covers the whole bottom of the
page" complaint, but there's still no way to move it out of the way of
existing content in that corner, resize it, or turn it off for a document
where it isn't wanted at all.

## Goals

- The stamp is a draggable, resizable field on the last page, using the same
  direct-manipulation interaction (drag body to move, corner handles to
  resize) already built for signature and text fields.
- It can be turned off entirely for a given document.
- Resizing it visibly resizes the QR code and text, not just empty padding —
  and is capped so it can never regress into a full-width banner.
- What you see while placing it is what ends up in the final PDF (real,
  scannable QR in the live preview, not a placeholder icon).

## Non-goals

- Moving the stamp to a page other than the last.
- Remembering a placement/on-off preference across documents or sessions —
  every new document starts from the same default.
- Editing the stamp's text content or QR error-correction level.
- Any chain/event changes — the stamp is cosmetic, not a signature act.

## A. Data model

The stamp's rect reuses the existing `FieldLocation` type (`page, x, y,
width, height` in PDF points) as-is — no new type needed, since its shape is
identical to what signature/text fields already use.

`assent-app/src/routes/Sign.tsx` adds two pieces of state:

```ts
const [stampEnabled, setStampEnabled] = useState(true);
const [stampField, setStampField] = useState<FieldLocation | null>(null);
```

`stampField` is seeded once, the first time `PdfViewer`'s `onDocumentReady`
fires (it already hands back every rendered page's dimensions, including the
last one) — bottom-right of the last page, same default size the current
fixed stamp uses. `stampEnabled` is a single on/off switch; both the sidebar
checkbox ("Include verification stamp," checked by default) and the
overlay's own remove button (×) just flip it off. The rect itself is
preserved independently of the enabled flag, so re-enabling restores the
last position instead of resetting to default — turning it off is never
destructive.

## B. Interaction

New component `StampFieldOverlay.tsx`, structurally the same pattern as
`SignatureField.tsx` / `TextFieldOverlay.tsx`: an absolutely-positioned div
using the shared `useDraggableResizable` hook for drag-to-move and
corner-handle resize. Two differences from how signature/text fields use
that hook:

- **A size cap.** The hook gains optional `maxWidthPt`/`maxHeightPt` params
  (signature/text fields don't pass them, so their behavior is unchanged).
  The stamp uses them to bound resize between roughly 90×50pt (below which a
  QR stops reliably scanning) and 320×140pt (about half the width of a US
  Letter page) — so no matter how a handle is dragged, it can't become the
  full-width banner that caused the original complaint.
- **Content that scales with the box**, not just the box growing around
  fixed-size content. A shared `computeStampLayout(width, height)` helper
  (`lib/pdf.ts`) derives QR size and font sizes as a function of the box's
  current height, clamped to legible bounds. Both the live preview
  (`StampFieldOverlay`) and the final PDF draw (`flattenSignedPdf`) call this
  same helper, so what's dragged/resized during placement matches the
  finished output exactly.

The live preview renders a **real QR code**, not a placeholder — the verify
URL (`{origin}/verify/{documentId}`) is known as soon as the document loads,
so a shared `generateVerifyQrDataUrl(url)` helper (`lib/pdf.ts`, wrapping the
existing `qrcode` call) runs once on mount and the resulting data URL is
reused for both the on-canvas preview and the final embed — one QR image
generated, not two.

The field is editable (draggable/resizable/removable) under the same
condition text fields already use — `stage.step === "placing"` — and
renders locked (no handles, no remove button) during `"review"`, consistent
with every other field type.

## C. Flatten changes

`FlattenArgs` (`assent-app/src/lib/pdf.ts`) gains a `stamp:
{ page: number; x: number; y: number; width: number; height: number } |
null` field, replacing today's hardcoded `STAMP_WIDTH`/`STAMP_HEIGHT`/
`QR_SIZE`/`STAMP_MARGIN` constants. `null` means the stamp is skipped
entirely (the `stampEnabled` off case). When present, `flattenSignedPdf`
draws at exactly that rect, using `computeStampLayout` for QR/font sizing —
the same function the live preview used, so resize behavior is identical in
both places.

`finishAndPublish` (`Sign.tsx`) passes `stampEnabled && stampField ? stampField : null`.

No chain/event changes — `field_added`/`signed` events stay specific to
signature and text fields; the stamp was never part of that model and
doesn't need to be.

## Testing

Same convention as the rest of `assent-app/`: no test framework, verify via
`tsc --noEmit` + manual QA in the browser — place/resize/move the stamp,
confirm the live QR is scannable and matches the finished PDF's QR exactly,
confirm the size caps hold at both extremes, confirm toggling off and back
on preserves position, confirm a document with the stamp disabled produces
no stamp on the finished PDF.
