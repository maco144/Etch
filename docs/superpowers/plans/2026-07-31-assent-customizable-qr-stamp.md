# Assent Customizable Verification Stamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the verify-QR stamp on the last page draggable, resizable (within size caps), and toggleable off entirely — replacing today's fixed 200×68pt bottom-right box.

**Architecture:** Reuses the drag/resize infrastructure already built for signature and text fields (`useDraggableResizable`, corner-handle pattern). Two pieces: (1) `lib/pdf.ts` gets a `stamp: FieldLocation | null` parameter on `flattenSignedPdf`, replacing the hardcoded stamp constants, with a shared `computeStampLayout(width, height)` helper that scales the QR/text proportionally to the box size; (2) a new `StampFieldOverlay.tsx` component renders a live, real-QR preview on the canvas using the same hook, wired into `Sign.tsx` with a default-seeded rect and a sidebar on/off toggle.

**Tech Stack:** React 18, TypeScript (`strict: true`, `noUnusedLocals`, `noUnusedParameters`), Tailwind, pdf-lib, qrcode.

**Design doc:** `docs/superpowers/specs/2026-07-31-assent-customizable-qr-stamp-design.md`

## Global Constraints

- No new npm dependencies. No test framework — verification per task is `tsc --noEmit` (`npm run lint`) plus manual QA in the browser (`npm run dev`), matching this project's existing convention.
- `lib/pdf.ts` cannot be imported directly from a plain Node script for verification — its top-level `import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"` is a Vite-only import that throws outside a Vite build. Task 1's verification script therefore reimplements `computeStampLayout`'s formula inline rather than importing it, and is discarded after use — it's a throwaway check, not a permanent test file.
- If `npm run build` is run to sanity-check bundling, it regenerates the tracked `site/assent/` deploy directory — revert that with `git restore`/`rm` before committing, exactly as in the prior session's work on this app. Don't deploy from this plan; that's a separate step the user asked for explicitly last time and will again if wanted.
- Corner-only resize handles (nw/ne/sw/se), same as signature/text fields — no edge handles.
- The stamp is last-page-only — no UI for moving it to another page (out of scope per the design doc).
- Coordinates stay in PDF points on the wire (`FieldLocation`); only display math converts to pixels via `scaleX = pageWidthPx / pageWidthPt` / `scaleY`, exactly as the existing overlay components already do.

---

## Task 1: Stamp layout helper + `flattenSignedPdf` parameterization

**Files:**
- Modify: `assent-app/src/lib/pdf.ts` (add `computeStampLayout`, `generateVerifyQrDataUrl`; change `FlattenArgs`/`flattenSignedPdf`'s stamp handling)

**Interfaces:**
- Consumes: nothing new — `FieldLocation` (`lib/etch.ts`), existing `qrcode`/`pdf-lib` imports.
- Produces: `computeStampLayout(width: number, height: number): StampLayout` and `generateVerifyQrDataUrl(url: string): Promise<string>`, both exported from `lib/pdf.ts` — Task 2's `StampFieldOverlay.tsx` imports both. `FlattenArgs.stamp: FieldLocation | null` (new required field) and `FlattenArgs.verifyUrl` (unchanged, but now only used when `stamp` is non-null).

- [ ] **Step 1: Add `computeStampLayout` and `generateVerifyQrDataUrl`**

Add above `flattenSignedPdf` in `assent-app/src/lib/pdf.ts` (after the `SignatureFieldValue` interface, before `FlattenArgs`):

```ts
const STAMP_REFERENCE_HEIGHT = 68; // today's fixed height — scale is relative to this
const STAMP_PADDING = 8;
const STAMP_MIN_TEXT_SPACE = 50; // reserve at least this much width for text next to the QR

export interface StampLayout {
  qrSize: number;
  qrLeft: number;
  qrTop: number;
  textLeft: number;
  titleSize: number;
  detailSize: number;
  titleTop: number;
  detailTop1: number;
  detailTop2: number;
  textMaxChars: number;
}

const clampNum = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max);

/**
 * Derives QR size, font sizes, and element positions (all offsets measured
 * from the box's top-left, matching on-screen CSS convention) from the
 * stamp box's own width/height, so resizing the box visibly resizes its
 * content instead of just adding empty padding. Used identically by the
 * live preview (StampFieldOverlay) and the final PDF draw below, so what's
 * previewed while placing matches what's embedded exactly.
 */
export function computeStampLayout(width: number, height: number): StampLayout {
  const scale = height / STAMP_REFERENCE_HEIGHT;

  // QR size is driven by height, but capped by width too — an independently
  // resized narrow-but-tall box (min width, max height) must not let the QR
  // alone exceed the box's width and crowd out all the text next to it.
  const maxQrByWidth = width - STAMP_PADDING * 2 - STAMP_MIN_TEXT_SPACE;
  const qrSize = clampNum(Math.min(40 * scale, maxQrByWidth), 20, 100);

  const titleSize = clampNum(7 * scale, 6, 12);
  const detailSize = clampNum(6 * scale, 5, 10);

  const qrLeft = STAMP_PADDING;
  const qrTop = (height - qrSize) / 2;
  const textLeft = qrLeft + qrSize + STAMP_PADDING;

  const titleTop = 6;
  const detailTop1 = titleTop + titleSize + 6;
  const detailTop2 = detailTop1 + detailSize + 4;

  const availableTextWidth = Math.max(0, width - textLeft - STAMP_PADDING);
  const textMaxChars = Math.max(6, Math.floor(availableTextWidth / (detailSize * 0.55)));

  return { qrSize, qrLeft, qrTop, textLeft, titleSize, detailSize, titleTop, detailTop1, detailTop2, textMaxChars };
}

/** Shared QR generation so the live preview and the final embed use identical settings. */
export async function generateVerifyQrDataUrl(url: string): Promise<string> {
  return QRCode.toDataURL(url, {
    margin: 0,
    width: 128,
    color: { dark: "#0a0a0f", light: "#ffffff" },
  });
}
```

- [ ] **Step 2: Change `FlattenArgs` to take an optional stamp rect**

Replace `FlattenArgs` (`assent-app/src/lib/pdf.ts:79-86`):

```ts
export interface FlattenArgs {
  originalBytes: Uint8Array;
  signatures: { location: FieldLocation; png: string }[];
  textFields: TextFieldValue[];
  documentId: string;
  verifyUrl: string;
  signerLabel: string;
  stamp: FieldLocation | null;
}
```

- [ ] **Step 3: Replace the hardcoded stamp block with the parameterized version**

Replace the entire stamp section (`assent-app/src/lib/pdf.ts:136-195`, from the `// Audit stamp on the last page` comment through the last `drawText` call, i.e. everything between the signatures loop and the `// Stash receipt metadata` comment) with:

```ts
  // Verification stamp — optional, sized/positioned by the caller (the user
  // may have moved, resized, or disabled it in the UI). `null` means skip
  // it entirely.
  if (args.stamp) {
    if (args.stamp.page < 1 || args.stamp.page > pages.length) {
      throw new Error(`stamp page ${args.stamp.page} out of range`);
    }
    const stampPage = pages[args.stamp.page - 1];
    const { height: stampPageHeight } = stampPage.getSize();
    // Same top-left-origin → bottom-left-origin translation as everywhere
    // else in this function.
    const stampPdfY = stampPageHeight - args.stamp.y - args.stamp.height;
    const toPdfY = (topOffset: number, elementHeight: number) =>
      stampPdfY + (args.stamp!.height - topOffset - elementHeight);

    const layout = computeStampLayout(args.stamp.width, args.stamp.height);
    const qrDataUrl = await generateVerifyQrDataUrl(args.verifyUrl);
    const qrPng = await pdfDoc.embedPng(qrDataUrl);

    stampPage.drawRectangle({
      x: args.stamp.x,
      y: stampPdfY,
      width: args.stamp.width,
      height: args.stamp.height,
      color: rgb(0.97, 0.97, 1),
      borderColor: rgb(0.85, 0.83, 0.95),
      borderWidth: 0.5,
    });

    stampPage.drawImage(qrPng, {
      x: args.stamp.x + layout.qrLeft,
      y: toPdfY(layout.qrTop, layout.qrSize),
      width: layout.qrSize,
      height: layout.qrSize,
    });

    const truncate = (s: string, max: number) => (s.length > max ? `${s.slice(0, max - 1)}…` : s);

    stampPage.drawText("Verified via Etch Assent", {
      x: args.stamp.x + layout.textLeft,
      y: toPdfY(layout.titleTop, layout.titleSize),
      size: layout.titleSize,
      font,
      color: rgb(0.1, 0.1, 0.15),
    });
    stampPage.drawText(truncate(`Doc: ${args.documentId}`, layout.textMaxChars), {
      x: args.stamp.x + layout.textLeft,
      y: toPdfY(layout.detailTop1, layout.detailSize),
      size: layout.detailSize,
      font,
      color: rgb(0.3, 0.3, 0.4),
    });
    stampPage.drawText(truncate(`Signer: ${args.signerLabel}`, layout.textMaxChars), {
      x: args.stamp.x + layout.textLeft,
      y: toPdfY(layout.detailTop2, layout.detailSize),
      size: layout.detailSize,
      font,
      color: rgb(0.3, 0.3, 0.4),
    });
  }
```

The `// Stash receipt metadata...` block and everything after it (`pdfDoc.setSubject`/`setKeywords`/`setProducer`/`pdfDoc.save`) is unchanged and stays outside this `if` — PDF metadata tagging happens regardless of whether the visual stamp is included.

- [ ] **Step 4: Typecheck**

Run: `cd assent-app && npm run lint`
Expected: no errors. (`Sign.tsx` will fail here — `flattenSignedPdf`'s only caller doesn't pass `stamp` yet. That's expected and fixed in Task 2; don't add a temporary default here, since Task 2 lands in the same session immediately after.)

- [ ] **Step 5: Verify the layout math with a throwaway Node script**

`lib/pdf.ts` can't be imported directly from plain Node (its top-level `?url` import is Vite-only), so this script reimplements just the `computeStampLayout` formula inline to check it against the min/default/max sizes the UI will allow, plus the narrow-tall edge case. Create it temporarily, run it, then delete it — it's not a permanent test file.

Create `assent-app/verify_stamp_layout.mjs`:

```js
import { PDFDocument, StandardFonts } from "pdf-lib";

const STAMP_REFERENCE_HEIGHT = 68;
const STAMP_PADDING = 8;
const STAMP_MIN_TEXT_SPACE = 50;
const clampNum = (v, min, max) => Math.min(Math.max(v, min), max);

function computeStampLayout(width, height) {
  const scale = height / STAMP_REFERENCE_HEIGHT;
  const maxQrByWidth = width - STAMP_PADDING * 2 - STAMP_MIN_TEXT_SPACE;
  const qrSize = clampNum(Math.min(40 * scale, maxQrByWidth), 20, 100);
  const titleSize = clampNum(7 * scale, 6, 12);
  const detailSize = clampNum(6 * scale, 5, 10);
  const qrLeft = STAMP_PADDING;
  const qrTop = (height - qrSize) / 2;
  const textLeft = qrLeft + qrSize + STAMP_PADDING;
  const titleTop = 6;
  const detailTop1 = titleTop + titleSize + 6;
  const detailTop2 = detailTop1 + detailSize + 4;
  const availableTextWidth = Math.max(0, width - textLeft - STAMP_PADDING);
  const textMaxChars = Math.max(6, Math.floor(availableTextWidth / (detailSize * 0.55)));
  return { qrSize, qrLeft, qrTop, textLeft, titleSize, detailSize, titleTop, detailTop1, detailTop2, textMaxChars };
}

async function check(label, width, height) {
  const doc = await PDFDocument.create();
  const font = await doc.embedFont(StandardFonts.Helvetica);
  const l = computeStampLayout(width, height);
  console.log(`\n${label}: box ${width}x${height}`);
  console.log(" layout:", l);

  const issues = [];
  if (l.qrLeft + l.qrSize > width) issues.push("QR exceeds box width");
  if (l.qrTop + l.qrSize > height || l.qrTop < 0) issues.push("QR exceeds box height");
  if (l.textLeft + STAMP_PADDING > width) issues.push("no room for text at all");
  const detail2Bottom = l.detailTop2 + l.detailSize;
  if (detail2Bottom > height) issues.push(`3rd text line (bottom ${detail2Bottom}) exceeds box height ${height}`);
  const titleW = font.widthOfTextAtSize("Verified via Etch Assent", l.titleSize);
  const availW = width - l.textLeft - STAMP_PADDING;
  if (titleW > availW) issues.push(`title text width ${titleW.toFixed(1)} exceeds available ${availW.toFixed(1)}`);

  console.log(issues.length ? " ISSUES: " + issues.join("; ") : " OK — no overflow");
}

await check("min size", 90, 50);
await check("default size", 200, 68);
await check("max size", 320, 140);
await check("narrow+tall edge case", 90, 140);
await check("wide+short edge case", 320, 50);
```

Run: `cd assent-app && node verify_stamp_layout.mjs`
Expected: "OK — no overflow" for all five cases. If any report issues, adjust `computeStampLayout`'s constants (in both this script and Step 1's real version) until they don't, then re-run.

Delete the script when done: `rm assent-app/verify_stamp_layout.mjs`

- [ ] **Step 6: Commit**

Task 1 alone won't typecheck clean (Task 2 fixes `Sign.tsx`'s call site) — commit both together at the end of Task 2, same reasoning as the earlier resize/multi-signature plan's Task 1+2 merge. Skip committing here; continue directly to Task 2.

---

## Task 2: Draggable/resizable stamp field wired into the signing flow

**Files:**
- Modify: `assent-app/src/hooks/useDraggableResizable.ts` (add optional `maxWidthPt`/`maxHeightPt`)
- Create: `assent-app/src/components/StampFieldOverlay.tsx`
- Modify: `assent-app/src/routes/Sign.tsx` (state, default-seeding, overlay rendering, sidebar toggle, `finishAndPublish`)

**Interfaces:**
- Consumes: Task 1's `computeStampLayout`, `generateVerifyQrDataUrl`, `FlattenArgs.stamp`.
- Produces: `useDraggableResizable`'s new `maxWidthPt`/`maxHeightPt` args (optional, default `Infinity` — existing signature/text field call sites are unaffected). `StampFieldOverlay` component. `Sign.tsx`'s `stampEnabled`/`stampField` state and `updateStampField` helper.

- [ ] **Step 1: Add max-size clamping to the shared hook**

In `assent-app/src/hooks/useDraggableResizable.ts`, add to `UseDraggableResizableArgs` (after `minHeightPt?: number;`):

```ts
  maxWidthPt?: number;
  maxHeightPt?: number;
```

Add defaults next to the existing `DEFAULT_MIN_WIDTH_PT`/`DEFAULT_MIN_HEIGHT_PT`:

```ts
const DEFAULT_MAX_WIDTH_PT = Infinity;
const DEFAULT_MAX_HEIGHT_PT = Infinity;
```

Destructure them in the hook's params (next to `minWidthPt = DEFAULT_MIN_WIDTH_PT,`):

```ts
  maxWidthPt = DEFAULT_MAX_WIDTH_PT,
  maxHeightPt = DEFAULT_MAX_HEIGHT_PT,
```

Add both to the `onPointerMove` `useCallback` dependency array (next to `minWidthPt, minHeightPt,`).

Replace the four resize cases in `onPointerMove` (each corner's upper/lower bound needs a max-size-aware counterpart on the edge that moves):

```ts
      switch (drag.corner) {
        case "nw": {
          const left = clamp(left0 + deltaXpt, Math.max(0, right0 - maxWidthPt), right0 - minWidthPt);
          const top = clamp(top0 + deltaYpt, Math.max(0, bottom0 - maxHeightPt), bottom0 - minHeightPt);
          onChange({ x: left, y: top, width: right0 - left, height: bottom0 - top });
          break;
        }
        case "ne": {
          const right = clamp(right0 + deltaXpt, left0 + minWidthPt, Math.min(pageWidthPt, left0 + maxWidthPt));
          const top = clamp(top0 + deltaYpt, Math.max(0, bottom0 - maxHeightPt), bottom0 - minHeightPt);
          onChange({ x: left0, y: top, width: right - left0, height: bottom0 - top });
          break;
        }
        case "sw": {
          const left = clamp(left0 + deltaXpt, Math.max(0, right0 - maxWidthPt), right0 - minWidthPt);
          const bottom = clamp(bottom0 + deltaYpt, top0 + minHeightPt, Math.min(pageHeightPt, top0 + maxHeightPt));
          onChange({ x: left, y: top0, width: right0 - left, height: bottom - top0 });
          break;
        }
        case "se": {
          const right = clamp(right0 + deltaXpt, left0 + minWidthPt, Math.min(pageWidthPt, left0 + maxWidthPt));
          const bottom = clamp(bottom0 + deltaYpt, top0 + minHeightPt, Math.min(pageHeightPt, top0 + maxHeightPt));
          onChange({ x: left0, y: top0, width: right - left0, height: bottom - top0 });
          break;
        }
      }
```

With the `Infinity` defaults, `Math.max(0, right0 - Infinity)` is `0` and `Math.min(pageWidthPt, left0 + Infinity)` is `pageWidthPt` — identical to today's behavior for signature/text fields, which don't pass these new args.

- [ ] **Step 2: Create `StampFieldOverlay.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useDraggableResizable, type Corner } from "../hooks/useDraggableResizable";
import { computeStampLayout, generateVerifyQrDataUrl } from "../lib/pdf";
import type { FieldLocation } from "../lib/etch";

interface Props {
  field: FieldLocation;
  pageWidthPx: number;
  pageHeightPx: number;
  pageWidthPt: number;
  pageHeightPt: number;
  editable: boolean;
  verifyUrl: string;
  onChange: (rect: { x: number; y: number; width: number; height: number }) => void;
  onRemove: () => void;
}

const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];
const CORNER_STYLE: Record<Corner, React.CSSProperties> = {
  nw: { left: -5, top: -5, cursor: "nwse-resize" },
  ne: { right: -5, top: -5, cursor: "nesw-resize" },
  sw: { left: -5, bottom: -5, cursor: "nesw-resize" },
  se: { right: -5, bottom: -5, cursor: "nwse-resize" },
};

const STAMP_MIN_WIDTH_PT = 90;
const STAMP_MIN_HEIGHT_PT = 50;
const STAMP_MAX_WIDTH_PT = 320;
const STAMP_MAX_HEIGHT_PT = 140;

/**
 * Live preview of the verification stamp: draggable/resizable like other
 * fields, rendering the real QR (not a placeholder) so what's placed here
 * matches flattenSignedPdf's output exactly.
 */
export default function StampFieldOverlay({
  field,
  pageWidthPx,
  pageHeightPx,
  pageWidthPt,
  pageHeightPt,
  editable,
  verifyUrl,
  onChange,
  onRemove,
}: Props) {
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    generateVerifyQrDataUrl(verifyUrl).then((url) => {
      if (!cancelled) setQrDataUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, [verifyUrl]);

  const { bodyProps, handleProps } = useDraggableResizable({
    rect: field,
    pageWidthPt,
    pageHeightPt,
    pageWidthPx,
    pageHeightPx,
    minWidthPt: STAMP_MIN_WIDTH_PT,
    minHeightPt: STAMP_MIN_HEIGHT_PT,
    maxWidthPt: STAMP_MAX_WIDTH_PT,
    maxHeightPt: STAMP_MAX_HEIGHT_PT,
    disabled: !editable,
    onChange,
  });

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;
  const left = field.x * scaleX;
  const top = field.y * scaleY;
  const width = field.width * scaleX;
  const height = field.height * scaleY;

  const layout = computeStampLayout(field.width, field.height);

  return (
    <div
      {...bodyProps}
      onClick={(e) => e.stopPropagation()}
      className={`absolute border rounded-sm bg-white/95 touch-none ${
        editable
          ? "pointer-events-auto cursor-move border-dashed border-accent"
          : "pointer-events-none border-solid border-border/40"
      }`}
      style={{ left, top, width, height }}
    >
      {qrDataUrl && (
        <img
          src={qrDataUrl}
          alt="verify QR"
          className="absolute"
          style={{
            left: layout.qrLeft * scaleX,
            top: layout.qrTop * scaleY,
            width: layout.qrSize * scaleX,
            height: layout.qrSize * scaleY,
          }}
        />
      )}
      <div
        className="absolute text-black/80 whitespace-nowrap leading-none"
        style={{
          left: layout.textLeft * scaleX,
          top: layout.titleTop * scaleY,
          fontSize: layout.titleSize * scaleY,
        }}
      >
        Verified via Etch Assent
      </div>
      <div
        className="absolute text-black/60 whitespace-nowrap leading-none"
        style={{
          left: layout.textLeft * scaleX,
          top: layout.detailTop1 * scaleY,
          fontSize: layout.detailSize * scaleY,
        }}
      >
        Doc: (preview)
      </div>
      <div
        className="absolute text-black/60 whitespace-nowrap leading-none"
        style={{
          left: layout.textLeft * scaleX,
          top: layout.detailTop2 * scaleY,
          fontSize: layout.detailSize * scaleY,
        }}
      >
        Signer: (preview)
      </div>
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
            onRemove();
          }}
          className="pointer-events-auto absolute -top-7 right-0 text-xs text-text-dim hover:text-danger"
        >
          remove stamp
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add stamp state and default-seeding to `Sign.tsx`**

Add the import (next to the existing `TextFieldOverlay` import, `Sign.tsx:6`):

```ts
import StampFieldOverlay from "../components/StampFieldOverlay";
```

Add default-size constants next to `TEXT_FIELD_WIDTH_PT`/`HEIGHT_PT`/`FONT_PT` (`Sign.tsx:38-40`):

```ts
const DEFAULT_STAMP_WIDTH_PT = 200;
const DEFAULT_STAMP_HEIGHT_PT = 68;
const DEFAULT_STAMP_MARGIN_PT = 24;
```

Add state next to `signatureFields`/`textFields` (`Sign.tsx:68-73`):

```ts
  const [stampEnabled, setStampEnabled] = useState(true);
  const [stampField, setStampField] = useState<FieldLocation | null>(null);
```

Add `updateStampField` next to `updateSignatureField` (`Sign.tsx:274-279`):

```ts
  const updateStampField = useCallback(
    (rect: { x: number; y: number; width: number; height: number }) => {
      setStampField((prev) => (prev ? { ...prev, ...rect } : prev));
    },
    [],
  );
```

- [ ] **Step 4: Seed the default stamp rect once the document finishes rendering**

Replace the `onDocumentReady` prop (`Sign.tsx:546`):

```tsx
          onDocumentReady={({ pages }) => setPagesByNumber(pages)}
```

with:

```tsx
          onDocumentReady={({ numPages, pages }) => {
            setPagesByNumber(pages);
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
```

- [ ] **Step 5: Render the stamp overlay on its page**

Add a new memo next to `textOverlaysForActive` (`Sign.tsx:501-523`):

```tsx
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
```

Render it after the other two overlays (`Sign.tsx:553-554`):

```tsx
          {signatureOverlaysForActive}
          {textOverlaysForActive}
          {stampOverlayForActive}
```

- [ ] **Step 6: Add the sidebar toggle**

Insert a new section into the `stage.step === "placing"` card, after the "Sign" block and before the card's closing `</div>` (i.e. right after the `</div>` that closes the "Sign" section, `Sign.tsx:670` in the pre-Task-2 file):

```tsx
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
```

- [ ] **Step 7: Wire the stamp into `finishAndPublish`**

In `finishAndPublish`'s call to `flattenSignedPdf` (`Sign.tsx:387-397`), add the `stamp` field:

```ts
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
        stamp: stampEnabled ? stampField : null,
      });
```

- [ ] **Step 8: Typecheck**

Run: `cd assent-app && npm run lint`
Expected: no errors.

- [ ] **Step 9: Manual QA**

Run: `cd assent-app && npm run dev`.

- Load a multi-page PDF. Confirm the stamp appears automatically, bottom-right of the *last* page, with a real (scannable, if you have a phone handy) QR — not a placeholder.
- Drag the stamp to a different spot on the last page — confirm it moves and clamps at page edges.
- Drag a corner handle to shrink it toward the minimum — confirm the QR and text shrink together and stay legible, not just empty padding shrinking.
- Drag a corner handle to grow it toward the maximum — confirm it stops growing well short of spanning the page width (the whole point of this feature).
- Try dragging height to max while width is at min (the narrow-tall edge case) — confirm the QR doesn't overflow the box or crowd out all the text.
- Uncheck "Include verification stamp" — confirm the overlay disappears from the canvas. Re-check it — confirm it reappears in the *same* position you last left it, not reset to default.
- Click the stamp's own "remove stamp" button — confirm it behaves identically to unchecking the sidebar box (same toggle, not a separate deletion).
- Complete a full sign → review → Finish & Publish flow with the stamp moved/resized. Confirm the downloaded PDF's stamp position/size matches what was previewed.
- Complete a flow with the stamp disabled. Confirm the downloaded PDF has no stamp at all, and that the PDF's metadata (Properties → Subject/Keywords, or `pdfDoc.getSubject()`) still contains the `etch-assent:` / `document:` tags regardless.

- [ ] **Step 10: Commit**

```bash
git add assent-app/src/lib/pdf.ts assent-app/src/hooks/useDraggableResizable.ts assent-app/src/components/StampFieldOverlay.tsx assent-app/src/routes/Sign.tsx
git commit -m "$(cat <<'EOF'
feat(assent): draggable, resizable, toggleable verification stamp

The QR verification stamp on the last page is now a field like
signature/text fields — drag to move, corner-resize with size caps
(90x50 to 320x140pt) so it can never regress into a full-width
banner, and a sidebar checkbox to turn it off entirely. Resizing
scales the QR and text together via a shared layout helper used by
both the live preview and the final PDF embed, so what's placed
during signing matches the finished document exactly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
