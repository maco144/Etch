// PDF rendering (PDF.js) + editing (pdf-lib) glue. All operations run in the
// browser — the PDF bytes never leave the client.

import * as pdfjs from "pdfjs-dist";
import { PDFDocument, StandardFonts, rgb } from "pdf-lib";
import QRCode from "qrcode";
import type { FieldLocation } from "./etch";
import { toArrayBuffer } from "./hash";

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

export interface FlattenArgs {
  originalBytes: Uint8Array;
  signaturePng: string; // data URL
  location: FieldLocation; // in PDF points
  receiptId: string;
  documentId: string;
  verifyUrl: string;
  signerLabel: string;
}

/**
 * Stamp the signature PNG onto the target page, embed an audit footer on the
 * last page with the receipt ID and a QR code, and return the updated bytes.
 * The resulting PDF is what we hash for the `finalized` event.
 */
export async function flattenSignedPdf(args: FlattenArgs): Promise<Uint8Array> {
  const pdfDoc = await PDFDocument.load(args.originalBytes);
  const pages = pdfDoc.getPages();

  if (args.location.page < 1 || args.location.page > pages.length) {
    throw new Error(`signature page ${args.location.page} out of range`);
  }

  const sigPng = await pdfDoc.embedPng(args.signaturePng);
  const targetPage = pages[args.location.page - 1];
  const { height: pageHeight } = targetPage.getSize();

  // Our UI coordinate system has y=0 at the top; pdf-lib uses PDF points with
  // y=0 at the bottom. Translate here.
  const pdfY = pageHeight - args.location.y - args.location.height;
  targetPage.drawImage(sigPng, {
    x: args.location.x,
    y: pdfY,
    width: args.location.width,
    height: args.location.height,
  });

  // Audit watermark on the last page.
  const lastPage = pages[pages.length - 1];
  const font = await pdfDoc.embedFont(StandardFonts.Helvetica);
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
  lastPage.drawText(`Receipt: ${args.receiptId}`, {
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

  // Stash receipt metadata in the PDF info dictionary so an offline verifier
  // can pull it back out without parsing the visual watermark.
  pdfDoc.setSubject(`etch-assent:${args.receiptId}`);
  pdfDoc.setKeywords([
    "etch-assent",
    `receipt:${args.receiptId}`,
    `document:${args.documentId}`,
  ]);
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
