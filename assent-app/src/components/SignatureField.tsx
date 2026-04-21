import type { FieldLocation } from "../lib/etch";

interface Props {
  field: FieldLocation | null;
  pageWidthPx: number;
  pageHeightPx: number;
  pageWidthPt: number;
  pageHeightPt: number;
  signaturePngUrl: string | null;
  onMove?: (field: FieldLocation) => void;
  onClear?: () => void;
}

/**
 * Renders the currently-placed signature field as an overlay on the active
 * PDF page. Coordinates on the wire are in PDF points; we scale for display.
 */
export default function SignatureField({
  field,
  pageWidthPx,
  pageHeightPx,
  pageWidthPt,
  pageHeightPt,
  signaturePngUrl,
  onClear,
}: Props) {
  if (!field || field.width <= 0 || field.height <= 0) return null;

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;

  const left = field.x * scaleX;
  const top = field.y * scaleY;
  const width = field.width * scaleX;
  const height = field.height * scaleY;

  return (
    <div
      className="absolute border-2 border-dashed border-accent bg-accent/10 rounded-sm pointer-events-none"
      style={{
        left,
        top,
        width,
        height,
      }}
    >
      {signaturePngUrl ? (
        <img
          src={signaturePngUrl}
          alt="signature"
          className="w-full h-full object-contain"
        />
      ) : (
        <div className="flex flex-col items-center justify-center h-full">
          <span className="text-xs font-medium text-accent">Sign here</span>
          <span className="text-[10px] text-text-muted">click Sign to capture</span>
        </div>
      )}
      {onClear && (
        <button
          type="button"
          onClick={onClear}
          className="pointer-events-auto absolute -top-7 right-0 text-xs text-text-dim hover:text-danger"
        >
          remove field
        </button>
      )}
    </div>
  );
}
