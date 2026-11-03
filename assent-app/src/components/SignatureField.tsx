import { useDraggableResizable, type Corner } from "../hooks/useDraggableResizable";
import type { SignatureFieldValue } from "../lib/pdf";

interface Props {
  field: SignatureFieldValue;
  pageWidthPx: number;
  pageHeightPx: number;
  pageWidthPt: number;
  pageHeightPt: number;
  editable: boolean;
  onChange: (rect: { x: number; y: number; width: number; height: number }) => void;
  onSign: () => void;
  onRemove: () => void;
}

const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];
const CORNER_STYLE: Record<Corner, React.CSSProperties> = {
  nw: { left: -5, top: -5, cursor: "nwse-resize" },
  ne: { right: -5, top: -5, cursor: "nesw-resize" },
  sw: { left: -5, bottom: -5, cursor: "nesw-resize" },
  se: { right: -5, bottom: -5, cursor: "nwse-resize" },
};

/**
 * Renders one placed signature field as an overlay on its PDF page.
 * Coordinates on the wire are in PDF points; we scale for display.
 */
export default function SignatureField({
  field,
  pageWidthPx,
  pageHeightPx,
  pageWidthPt,
  pageHeightPt,
  editable,
  onChange,
  onSign,
  onRemove,
}: Props) {
  // Signed fields stay movable/resizable while placing — the ink is a PNG
  // that flattenSignedPdf contain-fits into whatever box is here at
  // finalize time. Only an unsigned field treats a click as "sign this".
  const interactive = editable;

  const { bodyProps, handleProps } = useDraggableResizable({
    rect: field,
    pageWidthPt,
    pageHeightPt,
    pageWidthPx,
    pageHeightPx,
    disabled: !interactive,
    onChange,
    onClick: interactive && !field.signed ? onSign : undefined,
  });

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;
  const left = field.x * scaleX;
  const top = field.y * scaleY;
  const width = field.width * scaleX;
  const height = field.height * scaleY;

  return (
    <div
      {...bodyProps}
      // Belt-and-suspenders alongside the hook's preventDefault: if a native
      // click still reaches this element (e.g. a real mouse click with no
      // pointerdown-driven drag at all), stop it from bubbling to the page's
      // place-a-field handler, same as TextFieldOverlay's `swallow` wrapper.
      onClick={(e) => e.stopPropagation()}
      className={`group absolute border-2 border-dashed rounded-sm touch-none ${
        field.signed
          ? "border-success bg-success/5"
          : field.suggested
            ? "border-accent/60 bg-accent/5"
            : "border-accent bg-accent/10"
      } ${interactive ? "pointer-events-auto cursor-move" : "pointer-events-none"}`}
      style={{ left, top, width, height }}
    >
      {field.signature ? (
        <img
          src={field.signature.pngDataUrl}
          alt="signature"
          className="w-full h-full object-contain pointer-events-none select-none"
          draggable={false}
        />
      ) : (
        <div className="flex flex-col items-center justify-center h-full">
          <span className="text-xs font-medium text-accent">{field.suggested ? "Sign here?" : "Sign here"}</span>
          <span className="text-[10px] text-text-muted">
            {field.suggested ? "suggested — click to sign, or remove" : "click to sign, drag to move"}
          </span>
        </div>
      )}
      {interactive &&
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
          // Hover-only, like the text-field toolbar: a detected form can put
          // several suggestions on one page.
          className="pointer-events-auto absolute -top-7 right-0 text-xs text-text-dim hover:text-danger opacity-0 group-hover:opacity-100 focus:opacity-100"
        >
          remove field
        </button>
      )}
    </div>
  );
}
