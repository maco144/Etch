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
  const interactive = editable && !field.signed;

  const { bodyProps, handleProps } = useDraggableResizable({
    rect: field,
    pageWidthPt,
    pageHeightPt,
    pageWidthPx,
    pageHeightPx,
    disabled: !interactive,
    onChange,
    onClick: interactive ? onSign : undefined,
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
      className={`absolute border-2 border-dashed rounded-sm touch-none ${
        field.signed ? "border-success bg-success/5" : "border-accent bg-accent/10"
      } ${interactive ? "pointer-events-auto cursor-move" : "pointer-events-none"}`}
      style={{ left, top, width, height }}
    >
      {field.signature ? (
        <img
          src={field.signature.pngDataUrl}
          alt="signature"
          className="w-full h-full object-contain"
        />
      ) : (
        <div className="flex flex-col items-center justify-center h-full">
          <span className="text-xs font-medium text-accent">Sign here</span>
          <span className="text-[10px] text-text-muted">click to sign, drag to move</span>
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
          className="pointer-events-auto absolute -top-7 right-0 text-xs text-text-dim hover:text-danger"
        >
          remove field
        </button>
      )}
    </div>
  );
}
