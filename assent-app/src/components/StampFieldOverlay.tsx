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
