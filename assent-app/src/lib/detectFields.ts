// Find the places on a PDF where a signer is meant to write, entirely in the
// browser — Assent's promise is that the document never leaves the device,
// so detection can't be a hosted service.
//
// Two sources, in order of trust:
//
//   1. AcroForm widgets — the PDF declares its own fields. Exact boxes, types
//      and names; this is what "fillable PDF" means.
//   2. Layout heuristics for flat PDFs — underscore runs, drawn underlines,
//      captions under a line ("Signature" / "Date"), small drawn squares and
//      ☐ glyphs, and a known label ("Name:") followed by blank space.
//
// A page that has AcroForm widgets skips the heuristics: the author already
// said where the fields are, and guessing on top would only add duplicates.
//
// All geometry is returned in the same space the rest of Assent uses: PDF
// points at scale 1, origin top-left (pdf.js viewport space).

import * as pdfjs from "pdfjs-dist";
import type { PdfDocProxy } from "./pdf";

export type FieldHint = "name" | "date" | "email" | "initials";

interface Box {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export type DetectedField =
  | (Box & {
      kind: "text";
      source: "acroform" | "heuristic";
      fontSize: number;
      value: string;
      label?: string;
      hint?: FieldHint;
      acroName?: string;
    })
  | (Box & { kind: "signature"; source: "acroform" | "heuristic"; label?: string; acroName?: string })
  | (Box & {
      kind: "checkbox";
      source: "acroform" | "heuristic";
      checked: boolean;
      label?: string;
      acroName?: string;
      /** Radio widgets share an acroName; this is the option this widget selects. */
      radioValue?: string;
    });

export interface DetectionResult {
  fields: DetectedField[];
  /** AcroForm field names Assent took over; the flattener strips their widgets. */
  acroNames: string[];
}

const MAX_FONT_PT = 12;
const MIN_FONT_PT = 8;
const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

export async function detectFields(doc: PdfDocProxy): Promise<DetectionResult> {
  const fields: DetectedField[] = [];
  const acroNames = new Set<string>();
  for (let n = 1; n <= doc.numPages; n++) {
    const page = await doc.getPage(n);
    const viewport = page.getViewport({ scale: 1 });
    const fromForm = await acroFormFields(page, viewport, n);
    if (fromForm.length) {
      for (const f of fromForm) {
        fields.push(f);
        if (f.acroName) acroNames.add(f.acroName);
      }
      continue;
    }
    try {
      fields.push(...(await heuristicFields(page, viewport, n)));
    } catch (err) {
      // A page whose content we can't walk just gets no suggestions — never
      // block signing on detection.
      console.warn(`[assent] field detection failed on page ${n}`, err);
    }
  }
  return { fields, acroNames: [...acroNames] };
}

// ---------------------------------------------------------------------------
// 1. AcroForm
// ---------------------------------------------------------------------------

type PdfPage = Awaited<ReturnType<PdfDocProxy["getPage"]>>;
type Viewport = ReturnType<PdfPage["getViewport"]>;

// The bits of pdf.js's widget annotation data we read. pdf.js doesn't export
// a type for it.
interface WidgetData {
  subtype: string;
  fieldType?: string;
  fieldName?: string;
  rect: number[];
  readOnly?: boolean;
  hidden?: boolean;
  annotationFlags?: number;
  fieldValue?: unknown;
  exportValue?: string;
  buttonValue?: string;
  checkBox?: boolean;
  radioButton?: boolean;
  pushButton?: boolean;
  defaultAppearanceData?: { fontSize?: number };
}

const FLAG_HIDDEN = 1 << 1;
const FLAG_NOVIEW = 1 << 5;

async function acroFormFields(page: PdfPage, viewport: Viewport, n: number): Promise<DetectedField[]> {
  const annots = (await page.getAnnotations({ intent: "display" })) as WidgetData[];
  const out: DetectedField[] = [];
  for (const a of annots) {
    if (a.subtype !== "Widget" || !a.fieldName) continue;
    if (a.hidden || ((a.annotationFlags ?? 0) & (FLAG_HIDDEN | FLAG_NOVIEW))) continue;
    if (a.readOnly) continue;
    const [x1, y1, x2, y2] = viewport.convertToViewportRectangle(a.rect);
    const box = {
      page: n,
      x: Math.min(x1, x2),
      y: Math.min(y1, y2),
      width: Math.abs(x2 - x1),
      height: Math.abs(y2 - y1),
    };
    if (box.width < 2 || box.height < 2) continue;
    const label = humanizeFieldName(a.fieldName);

    switch (a.fieldType) {
      case "Tx": {
        const daSize = a.defaultAppearanceData?.fontSize ?? 0;
        // DA size 0 means "auto" — pick what fits one line of the box.
        const fontSize = clamp(daSize > 0 ? daSize : box.height * 0.7, MIN_FONT_PT - 2, MAX_FONT_PT);
        const value = typeof a.fieldValue === "string" ? a.fieldValue : "";
        out.push({
          kind: "text",
          source: "acroform",
          ...box,
          fontSize,
          value,
          label,
          hint: hintFor(label),
          acroName: a.fieldName,
        });
        break;
      }
      case "Btn": {
        if (a.pushButton) break;
        if (a.radioButton) {
          out.push({
            kind: "checkbox",
            source: "acroform",
            ...box,
            checked: a.fieldValue != null && a.fieldValue === a.buttonValue,
            label,
            acroName: a.fieldName,
            radioValue: a.buttonValue,
          });
        } else {
          const v = a.fieldValue;
          out.push({
            kind: "checkbox",
            source: "acroform",
            ...box,
            checked: typeof v === "string" && v !== "Off" && v !== "",
            label,
            acroName: a.fieldName,
          });
        }
        break;
      }
      case "Sig":
        out.push({ kind: "signature", source: "acroform", ...box, label, acroName: a.fieldName });
        break;
      // "Ch" (dropdown/list) isn't supported yet — left interactive in the PDF.
    }
  }
  return out;
}

/** "topmostSubform[0].Page1[0].f1_01[0]" → "f1 01"; "SignerName" → "Signer Name". */
function humanizeFieldName(name: string): string {
  const leaf = name.split(".").pop() ?? name;
  return leaf
    .replace(/\[\d+\]$/, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_\-]+/g, " ")
    .trim();
}

// ---------------------------------------------------------------------------
// 2. Heuristics for flat PDFs
// ---------------------------------------------------------------------------

/** A run of text on the page, in top-left pt space. */
interface Run {
  str: string;
  x: number;
  /** Baseline. */
  y: number;
  width: number;
  fontSize: number;
}

interface HLine {
  x1: number;
  x2: number;
  y: number;
  /** Typed underscores are always deliberate blanks; drawn rules may be decoration. */
  typed?: boolean;
}

interface Square {
  x: number;
  y: number;
  size: number;
}

const UNDERSCORES = /_{3,}/g;
const CHECKBOX_GLYPHS = /[☐□❏❑▢]/g;
const MIN_LINE_PT = 36;

const SIGNATURE_LABEL = /\b(signature|signed|sign here)\b|^by$/;
const DATE_LABEL = /^(date|dated|date signed|signature date|today'?s date)$/;
const NAME_LABEL = /^(name|full name|print(ed)? name|print|signer'?s? name|your name|name \(print\)|printed name and title)$/;
const EMAIL_LABEL = /^(e-?mail|e-?mail address)$/;
const INITIALS_LABEL = /^(initials?|initial here)$/;
/** Labels worth a field even with no line drawn — "Name:" then blank space. */
const BARE_LABEL =
  /^(name|full name|print(ed)? name|date|dated|signature|signed|e-?mail|phone|telephone|address|city|state|zip|zip code|title|company|initials?|by)$/;

async function heuristicFields(page: PdfPage, viewport: Viewport, n: number): Promise<DetectedField[]> {
  const runs = await textRuns(page, viewport);
  const { lines, squares } = await graphics(page, viewport);

  // Underscores and ☐ glyphs are text, but they're blanks, not content. Split
  // them out so "is there text on this line?" checks don't trip on them.
  const blanks: HLine[] = [];
  const glyphBoxes: Square[] = [];
  const words: Run[] = [];
  for (const r of runs) {
    const charW = r.str.length ? r.width / r.str.length : 0;
    let rest = r.str;
    for (const m of r.str.matchAll(UNDERSCORES)) {
      blanks.push({
        x1: r.x + m.index! * charW,
        x2: r.x + (m.index! + m[0].length) * charW,
        y: r.y + r.fontSize * 0.12,
        typed: true,
      });
    }
    for (const m of r.str.matchAll(CHECKBOX_GLYPHS)) {
      const size = r.fontSize * 0.8;
      glyphBoxes.push({ x: r.x + m.index! * charW + (charW - size) / 2, y: r.y - size * 0.9, size });
    }
    rest = rest.replace(UNDERSCORES, (s) => " ".repeat(s.length)).replace(CHECKBOX_GLYPHS, " ");
    // Keep word geometry for the non-blank parts of the run.
    for (const m of rest.matchAll(/\S(?:.*\S)?/g)) {
      words.push({ str: m[0], x: r.x + m.index! * charW, y: r.y, width: m[0].length * charW, fontSize: r.fontSize });
    }
  }

  const out: DetectedField[] = [];
  const taken: Box[] = [];
  const free = (b: Box) => !taken.some((t) => overlap(t, b) > 0.3);
  const take = (f: DetectedField) => {
    if (!free(f)) return;
    taken.push(f);
    out.push(f);
  };

  // Lines that already have words sitting on them are underlined text or
  // table rules with content, not blanks.
  const occupied = (l: HLine) =>
    words.some((w) => w.y <= l.y + 1 && w.y >= l.y - w.fontSize * 1.4 && spanOverlap(w.x, w.x + w.width, l.x1, l.x2) > 0.25);

  const allLines = [...blanks.filter((b) => b.x2 - b.x1 >= 12), ...lines.filter((l) => !occupied(l))];
  allLines.sort((a, b) => a.y - b.y || a.x1 - b.x1);

  for (const l of allLines) {
    const left = labelLeftOf(words, l.x1, l.y);
    const below = captionBelow(words, l);
    const label = norm(left?.str ?? below?.str ?? "");
    const fontSize = clamp(left?.fontSize ?? below?.fontSize ?? 11, MIN_FONT_PT, MAX_FONT_PT);
    const width = l.x2 - l.x1;
    // An unlabeled drawn rule spanning much of the page is a separator.
    if (!l.typed && !label && width > viewport.width * 0.5) continue;
    if (SIGNATURE_LABEL.test(label)) {
      const height = clamp(width * 0.25, 28, 44);
      take({ kind: "signature", source: "heuristic", page: n, x: l.x1, y: l.y - height, width: Math.max(width, 120), height, label });
      continue;
    }
    const height = fontSize * 1.15 + 3;
    take({
      kind: "text",
      source: "heuristic",
      page: n,
      x: l.x1,
      y: l.y - height + 1,
      width,
      height,
      fontSize,
      value: "",
      label: label || undefined,
      hint: hintFor(label),
    });
  }

  for (const s of [...squares, ...glyphBoxes]) {
    const right = words.find((w) => Math.abs(w.y - (s.y + s.size * 0.85)) < s.size && w.x >= s.x + s.size && w.x - (s.x + s.size) < 24);
    take({ kind: "checkbox", source: "heuristic", page: n, x: s.x, y: s.y, width: s.size, height: s.size, checked: false, label: right?.str });
  }

  // "Name:" followed by nothing — a blank the author left with tabs or spaces.
  for (const w of words) {
    const m = /^(.{1,30}?):\s*$/.exec(w.str);
    if (!m) continue;
    const label = norm(m[1]);
    if (!BARE_LABEL.test(label)) continue;
    const startX = w.x + w.width + 4;
    const room = Math.min(220, viewport.width - 36 - startX);
    if (room < 60) continue;
    const blocked = words.some((o) => o !== w && Math.abs(o.y - w.y) < w.fontSize * 0.8 && o.x >= startX - 2 && o.x < startX + 60);
    if (blocked) continue;
    const fontSize = clamp(w.fontSize, MIN_FONT_PT, MAX_FONT_PT);
    if (SIGNATURE_LABEL.test(label)) {
      take({ kind: "signature", source: "heuristic", page: n, x: startX, y: w.y - 30, width: Math.min(room, 180), height: 36, label });
      continue;
    }
    const height = fontSize * 1.15 + 3;
    take({
      kind: "text",
      source: "heuristic",
      page: n,
      x: startX,
      y: w.y - height + fontSize * 0.3,
      width: Math.min(room, 160),
      height,
      fontSize,
      value: "",
      label,
      hint: hintFor(label),
    });
  }

  return out;
}

function norm(s: string): string {
  return s.toLowerCase().replace(/[:*]/g, "").replace(/\s+/g, " ").trim();
}

function hintFor(label: string): FieldHint | undefined {
  const l = norm(label);
  if (DATE_LABEL.test(l)) return "date";
  if (NAME_LABEL.test(l)) return "name";
  if (EMAIL_LABEL.test(l)) return "email";
  if (INITIALS_LABEL.test(l)) return "initials";
  return undefined;
}

/** Nearest word ending just left of `x` on the line whose baseline is ~`y`. */
function labelLeftOf(words: Run[], x: number, y: number): Run | undefined {
  let best: Run | undefined;
  for (const w of words) {
    const right = w.x + w.width;
    if (right > x + 2 || x - right > 160) continue;
    if (Math.abs(w.y - y) > w.fontSize * 0.9) continue;
    if (!best || right > best.x + best.width) best = w;
  }
  return best;
}

/** A caption printed just under a line — the "Signature   Date" row of a signature block. */
function captionBelow(words: Run[], l: HLine): Run | undefined {
  return words.find(
    (w) => w.y - w.fontSize > l.y - 2 && w.y - w.fontSize - l.y < 10 && spanOverlap(w.x, w.x + w.width, l.x1, l.x2) > 0.6,
  );
}

/** Fraction of [a1,a2] covered by [b1,b2]. */
function spanOverlap(a1: number, a2: number, b1: number, b2: number): number {
  const inter = Math.min(a2, b2) - Math.max(a1, b1);
  return inter <= 0 ? 0 : inter / Math.max(1e-6, a2 - a1);
}

/** Intersection over the smaller box's area. */
function overlap(a: Box, b: Box): number {
  if (a.page !== b.page) return 0;
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  if (w <= 0 || h <= 0) return 0;
  return (w * h) / Math.max(1e-6, Math.min(a.width * a.height, b.width * b.height));
}

async function textRuns(page: PdfPage, viewport: Viewport): Promise<Run[]> {
  const content = await page.getTextContent();
  const runs: Run[] = [];
  for (const item of content.items) {
    if (!("str" in item) || !item.str) continue;
    const t = pdfjs.Util.transform(viewport.transform, item.transform);
    // Rotated or skewed text isn't where form blanks live.
    if (Math.abs(t[1]) > 0.01 || Math.abs(t[2]) > 0.01) continue;
    const fontSize = Math.hypot(t[2], t[3]);
    if (fontSize < 3) continue;
    runs.push({ str: item.str, x: t[4], y: t[5], width: item.width * (viewport.scale || 1), fontSize });
  }
  return runs;
}

/**
 * Walk the page's drawing operators for horizontal rules and small squares.
 * Tracks the CTM through save/restore/transform and form XObjects so path
 * coordinates land in viewport space.
 */
async function graphics(page: PdfPage, viewport: Viewport): Promise<{ lines: HLine[]; squares: Square[] }> {
  const { OPS } = pdfjs;
  const list = await page.getOperatorList();
  const lines: HLine[] = [];
  const squares: Square[] = [];

  let ctm = viewport.transform.slice();
  const stack: number[][] = [];
  const apply = (x: number, y: number) => {
    const p = pdfjs.Util.applyTransform([x, y], ctm);
    return { x: p[0], y: p[1] };
  };

  type Seg = { kind: "rect"; x: number; y: number; w: number; h: number } | { kind: "poly"; pts: { x: number; y: number }[] };
  let pending: Seg[] = [];

  const STROKES = new Set([OPS.stroke, OPS.closeStroke, OPS.fillStroke, OPS.eoFillStroke, OPS.closeFillStroke, OPS.closeEOFillStroke]);
  const FILLS = new Set([OPS.fill, OPS.eoFill]);

  /** Rect in viewport space → underline or checkbox candidate. */
  const classifyRect = (x: number, y: number, w: number, h: number, stroked: boolean) => {
    if (h <= 2.5 && w >= MIN_LINE_PT && w < viewport.width * 0.9) {
      lines.push({ x1: x, x2: x + w, y: y + h });
    } else if (stroked && w >= 6 && w <= 20 && Math.abs(w - h) <= 2) {
      squares.push({ x, y, size: (w + h) / 2 });
    }
  };

  const flush = (stroked: boolean) => {
    for (const s of pending) {
      if (s.kind === "rect") {
        const a = apply(s.x, s.y);
        const b = apply(s.x + s.w, s.y + s.h);
        classifyRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y), stroked);
        continue;
      }
      // Many producers (Word, pdf-lib) draw boxes as four line segments
      // instead of the `re` operator. An axis-aligned closed 4-gon is a rect.
      const box = polyAsRect(s.pts);
      if (box) {
        classifyRect(box.x, box.y, box.w, box.h, stroked);
        continue;
      }
      if (stroked) {
        for (let i = 1; i < s.pts.length; i++) {
          const p = s.pts[i - 1];
          const q = s.pts[i];
          const w = Math.abs(q.x - p.x);
          if (Math.abs(q.y - p.y) < 0.6 && w >= MIN_LINE_PT && w < viewport.width * 0.9) {
            lines.push({ x1: Math.min(p.x, q.x), x2: Math.max(p.x, q.x), y: (p.y + q.y) / 2 });
          }
        }
      }
    }
    pending = [];
  };

  for (let i = 0; i < list.fnArray.length; i++) {
    const fn = list.fnArray[i];
    const args = list.argsArray[i];
    switch (fn) {
      case OPS.save:
        stack.push(ctm);
        break;
      case OPS.restore:
        ctm = stack.pop() ?? ctm;
        break;
      case OPS.transform:
        ctm = pdfjs.Util.transform(ctm, args);
        break;
      case OPS.paintFormXObjectBegin:
        stack.push(ctm);
        if (Array.isArray(args[0]) && args[0].length === 6) ctm = pdfjs.Util.transform(ctm, args[0]);
        break;
      case OPS.paintFormXObjectEnd:
        ctm = stack.pop() ?? ctm;
        break;
      case OPS.constructPath: {
        // pdf.js 4.x: [ops[], flat coords[], minMax]
        const [ops, coords] = args as [number[], number[]];
        let j = 0;
        let poly: { x: number; y: number }[] | null = null;
        for (const op of ops) {
          if (op === OPS.rectangle) {
            pending.push({ kind: "rect", x: coords[j], y: coords[j + 1], w: coords[j + 2], h: coords[j + 3] });
            j += 4;
          } else if (op === OPS.moveTo) {
            poly = [apply(coords[j], coords[j + 1])];
            pending.push({ kind: "poly", pts: poly });
            j += 2;
          } else if (op === OPS.lineTo) {
            const p = apply(coords[j], coords[j + 1]);
            if (poly) poly.push(p);
            else pending.push({ kind: "poly", pts: (poly = [p]) });
            j += 2;
          } else if (op === OPS.curveTo) {
            j += 6;
            poly = null;
          } else if (op === OPS.curveTo2 || op === OPS.curveTo3) {
            j += 4;
            poly = null;
          }
        }
        break;
      }
      default:
        if (STROKES.has(fn)) flush(true);
        else if (FILLS.has(fn)) flush(false);
        else if (fn === OPS.endPath) pending = [];
    }
  }

  return { lines: dedupeLines(lines), squares };
}

function polyAsRect(pts: { x: number; y: number }[]): { x: number; y: number; w: number; h: number } | null {
  if (pts.length < 4 || pts.length > 5) return null;
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  const w = Math.max(...xs) - x;
  const h = Math.max(...ys) - y;
  if (w < 1 || h < 1) return null;
  const tol = 0.6;
  const onEdge = (p: { x: number; y: number }) =>
    (Math.abs(p.x - x) < tol || Math.abs(p.x - x - w) < tol) && (Math.abs(p.y - y) < tol || Math.abs(p.y - y - h) < tol);
  if (!pts.every(onEdge)) return null;
  for (let i = 1; i < pts.length; i++) {
    const axis = Math.abs(pts[i].x - pts[i - 1].x) < tol || Math.abs(pts[i].y - pts[i - 1].y) < tol;
    if (!axis) return null;
  }
  return { x, y, w, h };
}

/** Rules drawn twice (stroke + fill, or a table border shared by two cells) collapse to one. */
function dedupeLines(lines: HLine[]): HLine[] {
  const out: HLine[] = [];
  for (const l of lines) {
    if (out.some((o) => Math.abs(o.y - l.y) < 2 && spanOverlap(l.x1, l.x2, o.x1, o.x2) > 0.8)) continue;
    out.push(l);
  }
  return out;
}
