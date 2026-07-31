// PDF rendering (PDF.js) + editing (pdf-lib) glue. All operations run in the
// browser — the PDF bytes never leave the client.

import * as pdfjs from "pdfjs-dist";
import { PDFDocument, StandardFonts, rgb } from "pdf-lib";
import QRCode from "qrcode";
import type { FieldLocation } from "./etch";
import { toArrayBuffer } from "./hash";
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
}

export interface SignatureFieldValue extends FieldLocation {
  id: string;
  label?: string; // optional display tag ("Signer 1") — no routing behind it, no input UI yet
  signed: boolean;
  signature?: CapturedSignature;
  signerLabel?: string;
  signedAt?: string;
}

export interface FlattenArgs {
  originalBytes: Uint8Array;
  signatures: { location: FieldLocation; png: string }[];
  textFields: TextFieldValue[];
  documentId: string;
  verifyUrl: string;
  signerLabel: string;
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

  // Text fields first, so signatures visually sit on top if they overlap.
  // Empty-value fields are skipped — a user who placed a field and typed
  // nothing clearly didn't want it baked in.
  for (const tf of args.textFields) {
    if (!tf.value.trim()) continue;
    if (tf.page < 1 || tf.page > pages.length) continue;
    const page = pages[tf.page - 1];
    const { height: pageHeightPt } = page.getSize();
    // Baseline sits slightly above the bottom of the field box.
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
    // Our UI coordinate system has y=0 at the top; pdf-lib uses PDF points
    // with y=0 at the bottom. Translate here.
    const pdfY = pageHeight - sig.location.y - sig.location.height;
    targetPage.drawImage(sigPng, {
      x: sig.location.x,
      y: pdfY,
      width: sig.location.width,
      height: sig.location.height,
    });
  }

  // Audit stamp on the last page — a compact badge in the bottom-right
  // corner, not a full-width banner, so it doesn't cover the page's own
  // content. The QR already encodes the verify URL, so the label text only
  // needs a short, truncated summary next to it.
  const lastPage = pages[pages.length - 1];
  const { width: lpW } = lastPage.getSize();

  const STAMP_WIDTH = 200;
  const STAMP_HEIGHT = 68;
  const STAMP_MARGIN = 24;
  const QR_SIZE = 40;

  const stampX = lpW - STAMP_MARGIN - STAMP_WIDTH;
  const stampY = STAMP_MARGIN;

  const qrDataUrl = await QRCode.toDataURL(args.verifyUrl, {
    margin: 0,
    width: 128,
    color: { dark: "#0a0a0f", light: "#ffffff" },
  });
  const qrPng = await pdfDoc.embedPng(qrDataUrl);

  lastPage.drawRectangle({
    x: stampX,
    y: stampY,
    width: STAMP_WIDTH,
    height: STAMP_HEIGHT,
    color: rgb(0.97, 0.97, 1),
    borderColor: rgb(0.85, 0.83, 0.95),
    borderWidth: 0.5,
  });

  const qrX = stampX + 8;
  const qrY = stampY + (STAMP_HEIGHT - QR_SIZE) / 2;
  lastPage.drawImage(qrPng, { x: qrX, y: qrY, width: QR_SIZE, height: QR_SIZE });

  const textX = qrX + QR_SIZE + 8;
  const truncate = (s: string, max: number) => (s.length > max ? `${s.slice(0, max - 1)}…` : s);

  lastPage.drawText("Verified via Etch Assent", {
    x: textX,
    y: stampY + STAMP_HEIGHT - 16,
    size: 7,
    font,
    color: rgb(0.1, 0.1, 0.15),
  });
  lastPage.drawText(truncate(`Doc: ${args.documentId}`, 30), {
    x: textX,
    y: stampY + STAMP_HEIGHT - 30,
    size: 6,
    font,
    color: rgb(0.3, 0.3, 0.4),
  });
  lastPage.drawText(truncate(`Signer: ${args.signerLabel}`, 30), {
    x: textX,
    y: stampY + STAMP_HEIGHT - 42,
    size: 6,
    font,
    color: rgb(0.3, 0.3, 0.4),
  });

  // Stash receipt metadata in the PDF info dictionary so an offline verifier
  // can pull it back out without parsing the visual watermark.
  pdfDoc.setSubject(`etch-assent:${args.documentId}`);
  pdfDoc.setKeywords(["etch-assent", `document:${args.documentId}`]);
  pdfDoc.setProducer("Etch Assent");

  return pdfDoc.save({ useObjectStreams: false });
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
