// PDF rendering (PDF.js) + editing (pdf-lib) glue. All operations run in the
// browser — the PDF bytes never leave the client.

import * as pdfjs from "pdfjs-dist";
import {
  PDFArray,
  PDFDict,
  PDFDocument,
  PDFHexString,
  PDFName,
  PDFString,
  StandardFontEmbedder,
  StandardFonts,
  rgb,
} from "pdf-lib";
import type { FontNames } from "@pdf-lib/standard-fonts";
import QRCode from "qrcode";
import type { FieldLocation } from "./etch";
import { toArrayBuffer } from "./hash";
import type { FieldHint } from "./detectFields";
import type { CapturedSignature } from "./signatures";

// Vite's bundler resolves ?url imports; the worker ships alongside the app so
// PDF.js can run its rendering pipeline off the main thread.
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — Vite asset import
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export interface RenderedPage {
  pageNumber: number;
  canvas: HTMLCanvasElement;
  widthPt: number;
  heightPt: number;
  widthPx: number;
  heightPx: number;
  scale: number;
}

export type PdfDocProxy = pdfjs.PDFDocumentProxy;

export async function loadPdfForRender(bytes: Uint8Array): Promise<PdfDocProxy> {
  // PDF.js mutates the buffer when loading. Copy so we can re-hash the original.
  const copy = new Uint8Array(bytes);
  const task = pdfjs.getDocument({ data: copy });
  return task.promise;
}

export async function renderPage(
  doc: PdfDocProxy,
  pageNumber: number,
  scale = 1.5,
): Promise<RenderedPage> {
  const page = await doc.getPage(pageNumber);
  const viewport = page.getViewport({ scale });
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  canvas.className = "pdf-page rounded-md";
  const ctx = canvas.getContext("2d")!;
  await page.render({ canvasContext: ctx, viewport }).promise;

  const unscaled = page.getViewport({ scale: 1 });
  return {
    pageNumber,
    canvas,
    widthPt: unscaled.width,
    heightPt: unscaled.height,
    widthPx: canvas.width,
    heightPx: canvas.height,
    scale,
  };
}

export interface TextFieldValue extends FieldLocation {
  id: string;
  value: string;
  fontSize: number;
  /**
   * UI-only: the box tracks the typed text as it changes. On by default;
   * a manual resize turns it off, the "fit" control turns it back on.
   */
  autoFit?: boolean;
  /** UI-only: width while empty (a detected blank keeps its line's length). */
  emptyWidth?: number;
  /** Placed by field detection rather than by a click. */
  detected?: boolean;
  /** What the document calls this blank ("Date", "Printed name"); shown as the placeholder. */
  label?: string;
  /** Prefill binding: a "name"/"email" field follows the signer identity panel. */
  hint?: FieldHint;
  /** The AcroForm field this came from; widgets sharing a name share a value. */
  acroName?: string;
}

export interface CheckboxFieldValue extends FieldLocation {
  id: string;
  checked: boolean;
  detected?: boolean;
  label?: string;
  acroName?: string;
  /** Set on radio options: checking one clears its siblings with the same acroName. */
  radioValue?: string;
}

/** Horizontal gap between a text field's left edge and its first glyph, in pt. */
export const TEXT_FIELD_INSET_PT = 2;
/** Width of an empty field, so there's something to click and type into. */
export const TEXT_FIELD_EMPTY_WIDTH_PT = 120;
/** Line box around the glyphs, as a multiple of font size. */
export const TEXT_FIELD_LINE_HEIGHT = 1.15;
/** Overlay input border (Tailwind border-2), in screen px. */
export const TEXT_FIELD_BORDER_PX = 2;
// Room for the caret past the last glyph, so a fitted field doesn't scroll.
const CARET_SLACK_PT = 1;

// The same AFM metrics flattenSignedPdf's embedded Helvetica uses, so a field
// trimmed in the UI hugs exactly the text that gets baked into the PDF.
let helvetica: StandardFontEmbedder | null = null;
export function measureHelvetica(text: string, size: number): number {
  helvetica ??= StandardFontEmbedder.for(StandardFonts.Helvetica as unknown as FontNames);
  return helvetica.widthOfTextAtSize(text, size);
}

/**
 * The box that exactly holds `text` at `fontSize`: one line tall, ending at
 * the last character. `scaleX`/`scaleY` are page px-per-pt, needed because
 * the overlay's border is in screen px. Empty text gets the empty width.
 */
export function fitTextBox(
  text: string,
  fontSize: number,
  scaleX: number,
  scaleY: number,
  emptyWidth = TEXT_FIELD_EMPTY_WIDTH_PT,
): { width: number; height: number } {
  const width = text.trim()
    ? measureHelvetica(text, fontSize) + TEXT_FIELD_INSET_PT * 2 + CARET_SLACK_PT + (TEXT_FIELD_BORDER_PX * 2) / scaleX
    : emptyWidth;
  const height = fontSize * TEXT_FIELD_LINE_HEIGHT + (TEXT_FIELD_BORDER_PX * 2) / scaleY;
  return { width, height };
}

export interface SignatureFieldValue extends FieldLocation {
  id: string;
  label?: string; // optional display tag ("Signer 1") — no routing behind it, no input UI yet
  signed: boolean;
  signature?: CapturedSignature;
  signerLabel?: string;
  signedAt?: string;
  /**
   * Detected, not yet accepted. A suggestion doesn't block finishing and isn't
   * on the chain; signing it is what accepts it (field_added, then signed).
   */
  suggested?: boolean;
  acroName?: string;
}

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

export interface FlattenArgs {
  originalBytes: Uint8Array;
  signatures: { location: FieldLocation; png: string }[];
  textFields: TextFieldValue[];
  checkboxes?: CheckboxFieldValue[];
  /**
   * AcroForm fields Assent drew itself. Their widgets are stripped, or a
   * viewer would paint the original (empty) field over the drawn value.
   */
  acroNames?: string[];
  documentId: string;
  verifyUrl: string;
  signerLabel: string;
  stamp: FieldLocation | null;
}

/**
 * Stamp every signature PNG onto its target page, embed an audit footer on
 * the last page with the document ID and a QR code, and return the updated
 * bytes. The resulting PDF is what we hash for the `finalized` event.
 */
export async function flattenSignedPdf(args: FlattenArgs): Promise<Uint8Array> {
  const pdfDoc = await PDFDocument.load(args.originalBytes);
  const pages = pdfDoc.getPages();

  const font = await pdfDoc.embedFont(StandardFonts.Helvetica);

  if (args.acroNames?.length) stripFormWidgets(pdfDoc, new Set(args.acroNames));

  for (const cb of args.checkboxes ?? []) {
    if (!cb.checked || cb.page < 1 || cb.page > pages.length) continue;
    const page = pages[cb.page - 1];
    const bottom = page.getSize().height - cb.y - cb.height;
    const ink = rgb(0.05, 0.05, 0.1);
    if (cb.radioValue !== undefined) {
      const r = Math.min(cb.width, cb.height) * 0.28;
      page.drawCircle({ x: cb.x + cb.width / 2, y: bottom + cb.height / 2, size: r, color: ink });
      continue;
    }
    // A check mark drawn as two strokes — no font, so no encoding to get wrong.
    const s = Math.min(cb.width, cb.height);
    const ox = cb.x + (cb.width - s) / 2;
    const oy = bottom + (cb.height - s) / 2;
    const thickness = Math.max(1, s * 0.12);
    page.drawLine({ start: { x: ox + s * 0.2, y: oy + s * 0.5 }, end: { x: ox + s * 0.42, y: oy + s * 0.25 }, thickness, color: ink });
    page.drawLine({ start: { x: ox + s * 0.42, y: oy + s * 0.25 }, end: { x: ox + s * 0.82, y: oy + s * 0.8 }, thickness, color: ink });
  }

  // Text fields first, so signatures visually sit on top if they overlap.
  // Empty-value fields are skipped — a user who placed a field and typed
  // nothing clearly didn't want it baked in.
  for (const tf of args.textFields) {
    if (!tf.value.trim()) continue;
    if (tf.page < 1 || tf.page > pages.length) continue;
    const page = pages[tf.page - 1];
    const { height: pageHeightPt } = page.getSize();
    // Center the ascender-to-descender band in the box, the way the browser
    // centers the input's text — so a vertically trimmed field still lands
    // where it looked in the editor instead of dropping its descenders.
    const glyphBand = font.heightAtSize(tf.fontSize, { descender: true });
    const descent = glyphBand - font.heightAtSize(tf.fontSize, { descender: false });
    const baselineY = pageHeightPt - tf.y - tf.height + (tf.height - glyphBand) / 2 + descent;
    page.drawText(tf.value, {
      x: tf.x + TEXT_FIELD_INSET_PT,
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
    // Our UI coordinate system has y=0 at the top; pdf-lib uses PDF points
    // with y=0 at the bottom. Translate here.
    const pdfY = pageHeight - sig.location.y - sig.location.height;
    // Contain-fit and center, matching the overlay's object-contain — the
    // field box is user-resizable, and stretching would distort the ink.
    const fit = sigPng.scaleToFit(sig.location.width, sig.location.height);
    targetPage.drawImage(sigPng, {
      x: sig.location.x + (sig.location.width - fit.width) / 2,
      y: pdfY + (sig.location.height - fit.height) / 2,
      width: fit.width,
      height: fit.height,
    });
  }

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

    stampPage.drawText(truncate("Verified via Etch Assent", layout.textMaxChars), {
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

  // Stash receipt metadata in the PDF info dictionary so an offline verifier
  // can pull it back out without parsing the visual watermark.
  pdfDoc.setSubject(`etch-assent:${args.documentId}`);
  pdfDoc.setKeywords(["etch-assent", `document:${args.documentId}`]);
  pdfDoc.setProducer("Etch Assent");

  return pdfDoc.save({ useObjectStreams: false });
}

/** Fully-qualified AcroForm name of a widget: its /T and every /Parent's, dot-joined. */
function qualifiedFieldName(dict: PDFDict): string | null {
  const parts: string[] = [];
  let node: PDFDict | undefined = dict;
  for (let depth = 0; node && depth < 32; depth++) {
    const t = node.lookup(PDFName.of("T"));
    if (t instanceof PDFString || t instanceof PDFHexString) parts.unshift(t.decodeText());
    const parent: unknown = node.lookup(PDFName.of("Parent"));
    node = parent instanceof PDFDict ? parent : undefined;
  }
  return parts.length ? parts.join(".") : null;
}

/**
 * Remove the widget annotations (and their AcroForm entries) for fields Assent
 * has drawn. Done by hand: pdf-lib's removeField looks widgets up through
 * their appearance streams and throws on the many real-world forms that
 * don't have one. Fields Assent doesn't handle (dropdowns, buttons) stay.
 */
function stripFormWidgets(pdfDoc: PDFDocument, names: Set<string>): void {
  for (const page of pdfDoc.getPages()) {
    const annots = page.node.lookup(PDFName.of("Annots"));
    if (!(annots instanceof PDFArray)) continue;
    for (let i = annots.size() - 1; i >= 0; i--) {
      const dict = annots.lookup(i);
      if (!(dict instanceof PDFDict)) continue;
      if (dict.lookup(PDFName.of("Subtype")) !== PDFName.of("Widget")) continue;
      const name = qualifiedFieldName(dict);
      if (!name || !names.has(name)) continue;
      annots.remove(i);
    }
  }
  try {
    const form = pdfDoc.getForm();
    for (const field of form.getFields()) {
      if (names.has(field.getName())) form.acroForm.removeField(field.acroField);
    }
  } catch (err) {
    // The widgets are already gone, which is what matters visually; a
    // malformed /AcroForm tree just keeps its stale entries.
    console.warn("[assent] could not prune AcroForm entries", err);
  }
}

export function downloadBytes(bytes: Uint8Array, filename: string, mime = "application/pdf"): void {
  const blob = new Blob([toArrayBuffer(bytes)], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function downloadJson(value: unknown, filename: string): void {
  const bytes = new TextEncoder().encode(JSON.stringify(value, null, 2));
  downloadBytes(bytes, filename, "application/json");
}
