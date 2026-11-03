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
import { measureHelvetica, type PdfDocProxy } from "./pdf";

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
      /** Detected copies of one blank (a running page header) share a value. */
      group?: string;
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
      /** The page already shows a check here (a printed ☑), not just our overlay. */
      printedChecked?: boolean;
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
  linkRepeatedHeaders(fields);
  return { fields, acroNames: [...acroNames] };
}

/**
 * A blank with the same label at the same spot on several pages is a running
 * header ("Applicant name" on every page): give those one shared group so a
 * value typed once fills them all.
 */
function linkRepeatedHeaders(fields: DetectedField[]): void {
  const texts = fields.filter((f): f is Extract<DetectedField, { kind: "text" }> => f.kind === "text" && f.source === "heuristic" && !!f.label);
  for (const f of texts) {
    if (f.group) continue;
    const twins = texts.filter(
      (o) => o !== f && o.page !== f.page && o.label === f.label && Math.abs(o.x - f.x) < 3 && Math.abs(o.y - f.y) < 3,
    );
    if (!twins.length) continue;
    const key = `header:${f.label}:${Math.round(f.x)}:${Math.round(f.y)}`;
    f.group = key;
    for (const t of twins) t.group = key;
  }
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
  /** Font name, lowercased, alphanumerics only ("abcdefwingdingsregular"). */
  font: string;
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
  /** The document itself prints a check in this box (☑, Wingdings þ). */
  checked?: boolean;
}

/** A light-filled box — the "white field on a tinted panel" layout of designed forms. */
interface Panel {
  x: number;
  y: number;
  w: number;
  h: number;
  /** 0 (black) – 1 (white). */
  luma: number;
}

/** A vertical rule — only used to recognize table cells. */
interface VLine {
  x: number;
  y1: number;
  y2: number;
}

const UNDERSCORES = /_{3,}/g;
/** "( )", "(  )", "[ ]", "()" — a blank to mark. */
const BRACKET_BLANK = /\(\s{0,3}\)|\[\s{0,3}\]/g;

const UNICODE_BOX_EMPTY = new Set([..."☐□▢◻◽⬜❏❐❑❒"].concat(["\u{1F5C6}"]));
const UNICODE_BOX_CHECKED = new Set([..."☑☒"].concat(["\u{1F5F9}", "\u{1F5F7}"]));
// Symbol fonts reuse ASCII codes (or the same codes shifted into the U+F0xx
// private-use block, which is how Word's Insert > Symbol exports them), so
// the glyph depends on the font: 0xA3 is a box in Wingdings 2 and something
// else in Wingdings.
const SYMBOL_BOXES: { font: RegExp; empty: number[]; checked: number[] }[] = [
  { font: /wingdings2/, empty: [0xa3], checked: [0x52, 0x53, 0x54] },
  { font: /wingdings(?!2|3)/, empty: [0x6f, 0x70, 0x71, 0x72, 0xa8], checked: [0xfe, 0xfd, 0x78] },
  { font: /zapfdingbats|dingbats/, empty: [0x6f, 0x70, 0x71, 0x72], checked: [] },
];

/** Is this character a checkbox, and is it printed checked? */
function boxGlyph(ch: string, font: string): "empty" | "checked" | null {
  if (UNICODE_BOX_EMPTY.has(ch)) return "empty";
  if (UNICODE_BOX_CHECKED.has(ch)) return "checked";
  const cp = ch.codePointAt(0)!;
  const code = cp >= 0xf000 && cp <= 0xf0ff ? cp - 0xf000 : cp <= 0xff ? cp : -1;
  if (code < 0) return null;
  for (const s of SYMBOL_BOXES) {
    if (!s.font.test(font)) continue;
    if (s.empty.includes(code)) return "empty";
    if (s.checked.includes(code)) return "checked";
  }
  return null;
}

// Where a character sits inside a run. pdf.js gives only the run's total
// width; Helvetica's proportions are a much better split than equal widths
// (an underscore run after "Name: " starts well right of 6/30ths).
const advanceCache = new Map<string, number>();
function advance(ch: string): number {
  let w = advanceCache.get(ch);
  if (w === undefined) {
    try {
      w = measureHelvetica(ch, 1);
    } catch {
      w = 0.6; // outside WinAnsi — a symbol; call it a typical glyph width
    }
    advanceCache.set(ch, w);
  }
  return w;
}
function runOffsets(r: Run): number[] {
  // offsets[i] = x of code unit i's left edge; offsets[length] = right edge.
  const units = r.str.length;
  const adv: number[] = new Array(units).fill(0);
  let total = 0;
  for (let i = 0; i < units; ) {
    const cp = r.str.codePointAt(i)!;
    const len = cp > 0xffff ? 2 : 1;
    adv[i] = advance(String.fromCodePoint(cp));
    total += adv[i];
    i += len;
  }
  const out: number[] = [];
  let acc = 0;
  for (let i = 0; i <= units; i++) {
    out.push(r.x + (total > 0 ? (acc / total) * r.width : 0));
    if (i < units) acc += adv[i];
  }
  return out;
}
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
  // Operators first: walking them makes pdf.js load the page's fonts, and
  // the text pass needs font names to read symbol-font checkboxes.
  const { lines, verticals, squares, cells, fills } = await graphics(page, viewport);
  const runs = await textRuns(page, viewport);

  // Underscores, box glyphs and "( )" are text, but they're blanks, not
  // content. Split them out so "is there text on this line?" checks don't
  // trip on them.
  const blanks: HLine[] = [];
  const glyphBoxes: Square[] = [];
  const words: Run[] = [];
  for (const r of runs) {
    const at = runOffsets(r);
    const blanked = r.str.split("");
    const blank = (from: number, to: number) => {
      for (let i = from; i < to; i++) blanked[i] = " ";
    };
    for (const m of r.str.matchAll(UNDERSCORES)) {
      blanks.push({ x1: at[m.index!], x2: at[m.index! + m[0].length], y: r.y + r.fontSize * 0.12, typed: true });
      blank(m.index!, m.index! + m[0].length);
    }
    for (let i = 0; i < r.str.length; ) {
      const cp = r.str.codePointAt(i)!;
      const len = cp > 0xffff ? 2 : 1;
      const kind = boxGlyph(String.fromCodePoint(cp), r.font);
      if (kind) {
        // Box glyphs sit on the baseline and are about cap-height tall.
        const glyphW = at[i + len] - at[i];
        const size = Math.min(r.fontSize * 0.8, Math.max(glyphW, r.fontSize * 0.6));
        glyphBoxes.push({
          x: at[i] + (glyphW - size) / 2,
          y: r.y - size - r.fontSize * 0.02,
          size,
          checked: kind === "checked",
        });
        blank(i, i + len);
      }
      i += len;
    }
    for (const m of r.str.matchAll(BRACKET_BLANK)) {
      const x1 = at[m.index!];
      const x2 = at[m.index! + m[0].length];
      const size = r.fontSize * 0.62;
      glyphBoxes.push({ x: (x1 + x2) / 2 - size / 2, y: r.y - r.fontSize * 0.33 - size / 2, size });
      blank(m.index!, m.index! + m[0].length);
    }
    // Keep word geometry for the non-blank parts of the run.
    const rest = blanked.join("");
    for (const m of rest.matchAll(/\S(?:.*\S)?/g)) {
      words.push({ ...r, str: m[0], x: at[m.index!], width: at[m.index! + m[0].length] - at[m.index!] });
    }
  }

  // "OFFICE USE ONLY" / "ELRC USE ONLY" / "For office use": the panel under
  // that heading is for staff. Nothing in it is the signer's to fill.
  const staffOnly: Box[] = [];
  const headingRe = /\b(use only|for office use|office use|staff use|official use)\b/i;
  for (const w of words) {
    if (!headingRe.test(w.str)) continue;
    const around = fills
      .filter((f) => f.h > 40 && f.x <= w.x && f.y <= w.y - w.fontSize && f.x + f.w >= w.x + w.width && f.y + f.h >= w.y)
      .sort((a, b) => a.w * a.h - b.w * b.h)[0];
    if (around) staffOnly.push({ page: n, x: around.x, y: around.y, width: around.w, height: around.h });
  }

  const out: DetectedField[] = [];
  const taken: Box[] = [...staffOnly];
  const free = (b: Box) => !taken.some((t) => overlap(t, b) > 0.3);
  const take = (f: DetectedField) => {
    if (!free(f)) return;
    taken.push(f);
    out.push(f);
  };

  // Checkboxes first: they're unambiguous, and a box inside a field cell
  // means the cell is a multiple-choice row, not a write-in.
  for (const s of [...squares, ...glyphBoxes]) {
    const right = words.find((w) => Math.abs(w.y - (s.y + s.size * 0.85)) < s.size && w.x >= s.x + s.size && w.x - (s.x + s.size) < 24);
    take({
      kind: "checkbox",
      source: "heuristic",
      page: n,
      x: s.x,
      y: s.y,
      width: s.size,
      height: s.size,
      checked: !!s.checked,
      printedChecked: !!s.checked,
      label: right?.str,
    });
  }

  const boxes = [...squares, ...glyphBoxes];

  // Designed forms (InDesign, gov templates): each question is a light box on
  // a tinted panel, with the question printed at the top. The blank is the
  // empty space under the question — or right of a short "Label:".
  for (const c of cells) {
    const inside = words.filter((w) => w.x >= c.x - 1 && w.x + w.width <= c.x + c.w + 1 && w.y > c.y && w.y - w.fontSize * 0.2 <= c.y + c.h);
    if (boxes.some((b) => b.x >= c.x && b.y >= c.y && b.x + b.size <= c.x + c.w && b.y + b.size <= c.y + c.h)) continue;
    const baselines = [...new Set(inside.map((w) => Math.round(w.y)))].sort((a, b) => a - b);
    // A box with a paragraph in it is instructions, not a question.
    if (baselines.length > 2) continue;
    const top = inside.filter((w) => Math.round(w.y) === baselines[0]).sort((a, b) => a.x - b.x);
    // Questions are set flush-left in their box; a centered label is a
    // column header, and the blanks are in the rows under it.
    if (top.length && top[0].x - c.x > 12) {
      const textMid = (top[0].x + top[top.length - 1].x + top[top.length - 1].width) / 2;
      if (Math.abs(textMid - (c.x + c.w / 2)) < c.w * 0.12) continue;
    }
    const labelText = top.map((w) => w.str).join(" ");
    const fs = clamp(top[0]?.fontSize ?? 10, MIN_FONT_PT, MAX_FONT_PT);
    const height = fs * 1.15 + 3;
    const lowest = baselines.length ? Math.max(...inside.map((w) => w.y)) : c.y;
    const freeTop = baselines.length ? lowest + fs * 0.35 + 1 : c.y + 1;
    const room = c.y + c.h - 1 - freeTop;
    const label = norm(labelText);
    // A question answered by the Yes / No boxes on its own row needs no write-in.
    if (/\?\s*$/.test(labelText) && top.length) {
      const by = top[0].y;
      if (boxes.some((b) => Math.abs(b.y + b.size / 2 - (by - fs * 0.35)) < fs)) continue;
    }
    if (room >= height) {
      if (SIGNATURE_LABEL.test(label) && room >= 24) {
        take({ kind: "signature", source: "heuristic", page: n, x: c.x + 2, y: freeTop, width: c.w - 4, height: room, label });
        continue;
      }
      take({
        kind: "text",
        source: "heuristic",
        page: n,
        x: c.x + 3,
        y: freeTop + Math.min((room - height) / 2, fs * 0.6),
        width: c.w - 6,
        height,
        fontSize: fs,
        value: "",
        label: labelText || undefined,
        hint: hintFor(label) ?? looseHint(label),
      });
      continue;
    }
    // One line, no room below: the blank runs right of a "Label:" or a
    // question — never off the end of an ordinary sentence.
    const wordCount = labelText.trim().split(/\s+/).length;
    const shortLabel = (/:\s*$/.test(labelText) && wordCount <= 6) || (/\?\s*$/.test(labelText) && wordCount <= 12);
    if (baselines.length === 1 && top.length && shortLabel) {
      const last = top[top.length - 1];
      const startX = last.x + last.width + 4;
      if (c.x + c.w - 3 - startX >= 50) {
        take({
          kind: "text",
          source: "heuristic",
          page: n,
          x: startX,
          y: Math.max(c.y + 1, last.y - height + fs * 0.3),
          width: c.x + c.w - 3 - startX,
          height,
          fontSize: fs,
          value: "",
          label: labelText.replace(/:\s*$/, "") || undefined,
          hint: hintFor(label) ?? looseHint(label),
        });
      }
    }
  }

  // Table cells: a rule with vertical borders at both ends. The bottom of a
  // cell is where its blank would be — but only if nothing is written
  // anywhere in the cell. The top of a cell (borders hanging below it,
  // nothing above) is never a blank.
  const cellAbove = (l: HLine): number | null => {
    const up = (x: number) =>
      verticals.filter((v) => Math.abs(v.x - x) < 2 && v.y1 < l.y - 3 && v.y2 > l.y - 2).map((v) => v.y1);
    const left = up(l.x1);
    const right = up(l.x2);
    if (!left.length || !right.length) return null;
    return Math.max(Math.max(...left), Math.max(...right)); // the lower of the two tops
  };
  const cellTopOnly = (l: HLine) => {
    const down = (x: number) => verticals.some((v) => Math.abs(v.x - x) < 2 && v.y1 > l.y - 2 && v.y1 < l.y + 2 && v.y2 > l.y + 3);
    return down(l.x1) && down(l.x2) && cellAbove(l) === null;
  };

  // Lines that already have words sitting on them are underlined text or
  // table rules with content, not blanks.
  const occupied = (l: HLine) => {
    const top = cellAbove(l);
    return words.some(
      (w) =>
        w.y <= l.y + 1 &&
        w.y >= (top !== null ? top : l.y - w.fontSize * 1.4) &&
        spanOverlap(w.x, w.x + w.width, l.x1, l.x2) > 0.25,
    );
  };

  const allLines = [
    ...blanks.filter((b) => b.x2 - b.x1 >= 12),
    ...lines.filter(
      (l) =>
        !cellTopOnly(l) &&
        !occupied(l) &&
        // The top or bottom edge of a field box is its border, not a blank.
        !cells.some((c) => (Math.abs(l.y - c.y - c.h) < 2 || Math.abs(l.y - c.y) < 2) && spanOverlap(l.x1, l.x2, c.x, c.x + c.w) > 0.8),
    ),
  ];
  allLines.sort((a, b) => a.y - b.y || a.x1 - b.x1);

  for (const l of allLines) {
    // In a table the blank is the whole empty cell and its label is the cell
    // to its left in the same row — never text in the row below.
    const cellTop = l.typed ? null : cellAbove(l);
    const left =
      cellTop !== null
        ? nearestLeft(words.filter((w) => w.y > cellTop && w.y <= l.y + 1), l.x1, viewport.width)
        : labelLeftOf(words, l.x1, l.y);
    const below = cellTop !== null ? undefined : captionBelow(words, l);
    const label = norm(left?.str ?? below?.str ?? "");
    const fontSize = clamp(left?.fontSize ?? below?.fontSize ?? 11, MIN_FONT_PT, MAX_FONT_PT);
    const width = l.x2 - l.x1;
    // A drawn rule with no label anywhere near it is decoration (panel
    // edges, separators). Typed underscores are always deliberate.
    if (!l.typed && !label) continue;

    if (SIGNATURE_LABEL.test(label)) {
      // As tall as the ink wants, but never over the text above the line.
      const textAbove = words
        .filter((w) => w.y < l.y - 2 && spanOverlap(l.x1, l.x2, w.x, w.x + w.width) > 0 && w !== left)
        .map((w) => w.y + w.fontSize * 0.25);
      // …nor over the signature line above it in a stacked block.
      const linesAbove = [...lines, ...blanks]
        .filter((o) => o !== l && o.y < l.y - 4 && spanOverlap(l.x1, l.x2, o.x1, o.x2) > 0.2)
        .map((o) => o.y + 1);
      const ceilings = [...textAbove, ...linesAbove];
      const ceiling = ceilings.length ? Math.max(...ceilings) : -Infinity;
      const height = clamp(Math.min(width * 0.25, l.y - ceiling - 1), 14, 44);
      take({ kind: "signature", source: "heuristic", page: n, x: l.x1, y: l.y - height, width: Math.max(width, 120), height, label });
      continue;
    }
    const height = fontSize * 1.15 + 3;
    const cellH = cellTop !== null ? l.y - cellTop : 0;
    take({
      kind: "text",
      source: "heuristic",
      page: n,
      x: l.x1 + (cellTop !== null ? 2 : 0),
      // Centered in a cell; sitting on the line otherwise.
      y: cellH > height ? cellTop! + (cellH - height) / 2 : l.y - height + 1,
      width: cellTop !== null ? width - 4 : width,
      height,
      fontSize,
      value: "",
      label: label || undefined,
      hint: hintFor(label),
    });
  }

  // "Name:" followed by nothing — a blank the author left with tabs or spaces.
  // Labels inside a field box were already handled by the box.
  const inCell = (w: Run) => cells.some((c) => w.x >= c.x - 1 && w.x + w.width <= c.x + c.w + 1 && w.y > c.y && w.y <= c.y + c.h);
  for (const w of words) {
    if (inCell(w)) continue;
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

/** Question-style labels ("What is your email address?") — only the unambiguous ones. */
function looseHint(label: string): FieldHint | undefined {
  if (/\be-?mail\b/.test(label)) return "email";
  return undefined;
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
  return nearestLeft(
    words.filter((w) => Math.abs(w.y - y) <= w.fontSize * 0.9),
    x,
  );
}

/** Of `words`, the one whose right edge is closest to (and left of) `x`, within `maxGap`. */
function nearestLeft(words: Run[], x: number, maxGap = 160): Run | undefined {
  let best: Run | undefined;
  for (const w of words) {
    const right = w.x + w.width;
    if (right > x + 2 || x - right > maxGap) continue;
    if (!best || right > best.x + best.width) best = w;
  }
  return best;
}

/**
 * A caption printed just under a line — the "Signature   Date" row of a
 * signature block. Captions are a few words; a sentence under a rule is just
 * the next paragraph.
 */
function captionBelow(words: Run[], l: HLine): Run | undefined {
  return words.find(
    (w) =>
      w.y - w.fontSize > l.y - 2 &&
      w.y - w.fontSize - l.y < 10 &&
      spanOverlap(w.x, w.x + w.width, l.x1, l.x2) > 0.6 &&
      w.str.trim().split(/\s+/).length <= 4 &&
      // Flush with the line's start, or centered under it — not a word
      // that happens to fall under it mid-sentence.
      (Math.abs(w.x - l.x1) < 12 || Math.abs(w.x + w.width / 2 - (l.x1 + l.x2) / 2) < (l.x2 - l.x1) * 0.15),
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

/** The PDF's own font name for a pdf.js loaded-font id, normalized for matching. */
function fontNameOf(page: PdfPage, id: string): string {
  try {
    if (!page.commonObjs.has(id)) return "";
    const f = page.commonObjs.get(id) as { name?: string; loadedName?: string } | undefined;
    return (f?.name ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
  } catch {
    return "";
  }
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
    runs.push({ str: item.str, x: t[4], y: t[5], width: item.width * (viewport.scale || 1), fontSize, font: fontNameOf(page, item.fontName) });
  }
  return runs;
}

/**
 * Walk the page's drawing operators for horizontal rules and small squares.
 * Tracks the CTM through save/restore/transform and form XObjects so path
 * coordinates land in viewport space.
 */
async function graphics(
  page: PdfPage,
  viewport: Viewport,
): Promise<{ lines: HLine[]; verticals: VLine[]; squares: Square[]; cells: Panel[]; fills: Panel[] }> {
  const { OPS } = pdfjs;
  const list = await page.getOperatorList();
  const lines: HLine[] = [];
  const verticals: VLine[] = [];
  const squares: Square[] = [];
  // Filled squares are usually bullets (■) — unless one has a smaller square
  // filled inside it, which is how some producers draw a box's border.
  const filledSquares: Square[] = [];
  // Boxes drawn as four separate strokes or hairline rects.
  const shortH: HLine[] = [];
  const shortV: VLine[] = [];

  // Every filled rect, in paint order, with its color: cells are light boxes
  // painted over something darker, and a white rule on white is invisible.
  const fills: Panel[] = [];
  const whiteSquares: Square[] = [];

  let ctm = viewport.transform.slice();
  // pdf.js normalizes every color space to RGB bytes in the operator list.
  let fillLuma = 0;
  let strokeLuma = 0;
  const stack: { ctm: number[]; fillLuma: number; strokeLuma: number }[] = [];
  const luma = (r: number, g: number, b: number) => (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const INVISIBLE = 0.97;
  const apply = (x: number, y: number) => {
    const p = pdfjs.Util.applyTransform([x, y], ctm);
    return { x: p[0], y: p[1] };
  };

  type Seg = { kind: "rect"; x: number; y: number; w: number; h: number } | { kind: "poly"; pts: { x: number; y: number }[] };
  let pending: Seg[] = [];

  const STROKES = new Set([OPS.stroke, OPS.closeStroke, OPS.fillStroke, OPS.eoFillStroke, OPS.closeFillStroke, OPS.closeEOFillStroke]);
  const FILLS = new Set([OPS.fill, OPS.eoFill]);

  /** Rect in viewport space → underline or checkbox candidate. */
  const isBoxSize = (w: number, h: number) => w >= 5 && w <= 28 && Math.abs(w - h) <= Math.max(1.5, w * 0.12);
  const classifyRect = (x: number, y: number, w: number, h: number, stroked: boolean) => {
    const paint = stroked ? strokeLuma : fillLuma;
    if (!stroked) fills.push({ x, y, w, h, luma: fillLuma });
    if (paint > INVISIBLE) {
      // White paint: never a rule or a drawn box outline — but a white square
      // punched into a tinted panel is a checkbox.
      // (Small ones only: a 26pt white square is a one-letter write-in cell.)
      if (!stroked && isBoxSize(w, h) && w <= 16) whiteSquares.push({ x, y, size: (w + h) / 2 });
      return;
    }
    if (h <= 2.5 && w >= MIN_LINE_PT && w < viewport.width * 0.9) {
      lines.push({ x1: x, x2: x + w, y: y + h });
    } else if (h <= 2.5 && w >= 4 && w < MIN_LINE_PT) {
      shortH.push({ x1: x, x2: x + w, y: y + h / 2 });
    } else if (w <= 2.5 && h >= 4) {
      const v = { x: x + w / 2, y1: y, y2: y + h };
      verticals.push(v);
      if (h <= 28) shortV.push(v);
    } else if (isBoxSize(w, h)) {
      (stroked ? squares : filledSquares).push({ x, y, size: (w + h) / 2 });
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
      if (stroked && strokeLuma <= INVISIBLE) {
        for (let i = 1; i < s.pts.length; i++) {
          const p = s.pts[i - 1];
          const q = s.pts[i];
          const w = Math.abs(q.x - p.x);
          const h = Math.abs(q.y - p.y);
          if (h < 0.6 && w >= MIN_LINE_PT && w < viewport.width * 0.9) {
            lines.push({ x1: Math.min(p.x, q.x), x2: Math.max(p.x, q.x), y: (p.y + q.y) / 2 });
          } else if (h < 0.6 && w >= 4) {
            shortH.push({ x1: Math.min(p.x, q.x), x2: Math.max(p.x, q.x), y: (p.y + q.y) / 2 });
          } else if (w < 0.6 && h >= 4) {
            const v = { x: (p.x + q.x) / 2, y1: Math.min(p.y, q.y), y2: Math.max(p.y, q.y) };
            verticals.push(v);
            if (h <= 28) shortV.push(v);
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
        stack.push({ ctm, fillLuma, strokeLuma });
        break;
      case OPS.restore: {
        const st = stack.pop();
        if (st) ({ ctm, fillLuma, strokeLuma } = st);
        break;
      }
      case OPS.setFillRGBColor:
        fillLuma = luma(args[0], args[1], args[2]);
        break;
      case OPS.setStrokeRGBColor:
        strokeLuma = luma(args[0], args[1], args[2]);
        break;
      case OPS.transform:
        ctm = pdfjs.Util.transform(ctm, args);
        break;
      case OPS.paintFormXObjectBegin:
        stack.push({ ctm, fillLuma, strokeLuma });
        if (Array.isArray(args[0]) && args[0].length === 6) ctm = pdfjs.Util.transform(ctm, args[0]);
        break;
      case OPS.paintFormXObjectEnd: {
        const st = stack.pop();
        if (st) ({ ctm, fillLuma, strokeLuma } = st);
        break;
      }
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

  // Frames: a filled square with a smaller one inside (border = the ring).
  for (const outer of filledSquares) {
    const framed = filledSquares.some(
      (inner) =>
        inner !== outer &&
        inner.size < outer.size &&
        outer.size - inner.size <= 4 &&
        inner.x >= outer.x - 0.1 &&
        inner.y >= outer.y - 0.1 &&
        inner.x + inner.size <= outer.x + outer.size + 0.1 &&
        inner.y + inner.size <= outer.y + outer.size + 0.1,
    );
    if (framed) squares.push(outer);
  }
  // Four sides: two equal short horizontals one side-length apart, with
  // verticals closing both ends.
  for (const top of shortH) {
    const span = top.x2 - top.x1;
    if (span < 5 || span > 28) continue;
    const bottom = shortH.find(
      (b) => b !== top && Math.abs(b.x1 - top.x1) < 1 && Math.abs(b.x2 - top.x2) < 1 && Math.abs(b.y - top.y - span) < Math.max(1.5, span * 0.12),
    );
    if (!bottom) continue;
    const side = (x: number) => shortV.some((v) => Math.abs(v.x - x) < 1.2 && v.y1 <= top.y + 1 && v.y2 >= bottom.y - 1);
    if (side(top.x1) && side(top.x2)) squares.push({ x: top.x1, y: top.y, size: span });
  }

  // Visible = painted over a darker fill that was there first.
  const onDarker = (b: { x: number; y: number; w: number; h: number }, idx: number, l: number) =>
    fills.some(
      (f, j) =>
        j < idx &&
        f.luma < l - 0.04 &&
        f.x <= b.x + 0.5 &&
        f.y <= b.y + 0.5 &&
        f.x + f.w >= b.x + b.w - 0.5 &&
        f.y + f.h >= b.y + b.h - 0.5,
    );
  for (const sq of whiteSquares) {
    const idx = fills.findIndex((f) => Math.abs(f.x - sq.x) < 0.01 && Math.abs(f.y - sq.y) < 0.01);
    if (onDarker({ x: sq.x, y: sq.y, w: sq.size, h: sq.size }, idx, 1)) squares.push(sq);
  }
  const cells = fills.filter(
    (f, i) =>
      f.luma >= 0.75 &&
      f.w >= 18 &&
      f.h >= 12 &&
      f.h <= 220 &&
      f.w < viewport.width * 0.95 &&
      (f.luma < INVISIBLE || onDarker(f, i, f.luma)),
  );
  // Innermost only: a panel that contains other cells is a container.
  const innermost = cells.filter(
    (c) => !cells.some((o) => o !== c && o.x >= c.x - 0.5 && o.y >= c.y - 0.5 && o.x + o.w <= c.x + c.w + 0.5 && o.y + o.h <= c.y + c.h + 0.5 && o.w * o.h < c.w * c.h),
  );

  return { lines: dedupeLines(lines), verticals, squares: dedupeSquares(squares), cells: innermost, fills };
}

function dedupeSquares(squares: Square[]): Square[] {
  const out: Square[] = [];
  for (const s of squares) {
    if (out.some((o) => Math.abs(o.x - s.x) < 2 && Math.abs(o.y - s.y) < 2)) continue;
    out.push(s);
  }
  return out;
}

function polyAsRect(pts: { x: number; y: number }[]): { x: number; y: number; w: number; h: number } | null {
  // InDesign traces boxes with extra points — 13 for a square with stepped
  // corners. Still a rect if every point is on the boundary, every segment is
  // axis-aligned, and the walk covers the perimeter (checked below).
  if (pts.length < 4 || pts.length > 24) return null;
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  const w = Math.max(...xs) - x;
  const h = Math.max(...ys) - y;
  if (w < 1 || h < 1) return null;
  const tol = 0.6;
  // Points may start mid-edge (InDesign does), so: every point on the
  // boundary, every segment axis-aligned, and the segments walk (nearly) the
  // whole perimeter — that's a closed rectangle, not an L or a bracket.
  const onEdge = (p: { x: number; y: number }) =>
    Math.abs(p.x - x) < tol || Math.abs(p.x - x - w) < tol || Math.abs(p.y - y) < tol || Math.abs(p.y - y - h) < tol;
  if (!pts.every(onEdge)) return null;
  let walked = 0;
  for (let i = 1; i < pts.length; i++) {
    const dx = Math.abs(pts[i].x - pts[i - 1].x);
    const dy = Math.abs(pts[i].y - pts[i - 1].y);
    if (dx >= tol && dy >= tol) return null;
    walked += dx + dy;
  }
  const first = pts[0];
  const last = pts[pts.length - 1];
  walked += Math.abs(first.x - last.x) + Math.abs(first.y - last.y); // implicit close
  if (walked < 2 * (w + h) * 0.95) return null;
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
