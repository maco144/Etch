import { useEffect, useMemo, useRef, useState } from "react";
import {
  loadPdfForRender,
  renderPage,
  type PdfDocProxy,
  type RenderedPage,
} from "../lib/pdf";

interface Props {
  bytes: Uint8Array;
  activePage: number;
  onPageChange: (page: number) => void;
  onDocumentReady: (doc: { numPages: number; pages: Map<number, RenderedPage> }) => void;
  onPageClick?: (page: number, xPct: number, yPct: number) => void;
  children?: React.ReactNode;
}

/**
 * Renders every page of the PDF as a stacked column of canvases. The spec
 * targets single-signer flows, so we prioritize visibility (scroll to sign)
 * over virtualization. Most signing PDFs are 1–10 pages.
 */
export default function PdfViewer({
  bytes,
  activePage,
  onPageChange,
  onDocumentReady,
  onPageClick,
  children,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [rendered, setRendered] = useState<Map<number, RenderedPage>>(new Map());
  const [numPages, setNumPages] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const docRef = useRef<PdfDocProxy | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRendered(new Map());
    setError(null);
    setNumPages(0);

    (async () => {
      try {
        const doc = await loadPdfForRender(bytes);
        if (cancelled) return;
        docRef.current = doc;
        setNumPages(doc.numPages);

        const pageMap = new Map<number, RenderedPage>();
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await renderPage(doc, i, 1.5);
          if (cancelled) return;
          pageMap.set(i, page);
          setRendered(new Map(pageMap));
        }
        onDocumentReady({ numPages: doc.numPages, pages: pageMap });
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Could not render this PDF — ${msg}`);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bytes]);

  const pageNumbers = useMemo(() => {
    return Array.from({ length: numPages }, (_, i) => i + 1);
  }, [numPages]);

  if (error) {
    return (
      <div className="card p-6 text-danger text-sm">
        <strong>Failed to open PDF.</strong>
        <div className="mt-2 text-text-dim">{error}</div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="space-y-4">
      {pageNumbers.map((n) => {
        const rp = rendered.get(n);
        if (!rp) {
          return (
            <div
              key={n}
              className="card h-[800px] flex items-center justify-center text-text-muted text-sm"
            >
              Rendering page {n}…
            </div>
          );
        }
        return (
          <PageCanvas
            key={n}
            page={rp}
            active={n === activePage}
            onActivate={() => onPageChange(n)}
            onClick={onPageClick}
          >
            {n === activePage ? children : null}
          </PageCanvas>
        );
      })}
    </div>
  );
}

interface PageCanvasProps {
  page: RenderedPage;
  active: boolean;
  onActivate: () => void;
  onClick?: (page: number, xPct: number, yPct: number) => void;
  children?: React.ReactNode;
}

function PageCanvas({ page, active, onActivate, onClick, children }: PageCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    host.innerHTML = "";
    host.appendChild(page.canvas);
  }, [page]);

  return (
    <div
      className={`relative mx-auto ${active ? "ring-2 ring-accent/60" : ""}`}
      style={{ width: page.widthPx }}
      onMouseEnter={onActivate}
      onClick={(e) => {
        if (!onClick) return;
        const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
        const xPct = (e.clientX - rect.left) / rect.width;
        const yPct = (e.clientY - rect.top) / rect.height;
        onClick(page.pageNumber, xPct, yPct);
      }}
    >
      <div ref={hostRef} />
      <div
        className="absolute top-2 right-3 text-xs font-mono text-text-muted bg-bg/60 px-2 py-0.5 rounded"
        aria-hidden
      >
        {page.pageNumber}
      </div>
      {children}
    </div>
  );
}
