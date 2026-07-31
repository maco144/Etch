import { useEffect, useRef } from "react";
import type { TextFieldValue } from "../lib/pdf";
import { useDraggableResizable, type Corner } from "../hooks/useDraggableResizable";

interface Props {
  field: TextFieldValue;
  pageWidthPx: number;
  pageHeightPx: number;
  pageWidthPt: number;
  pageHeightPt: number;
  editable: boolean;
  autoFocus?: boolean;
  onChange: (next: TextFieldValue) => void;
  onRemove: (id: string) => void;
}

const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];
const CORNER_STYLE: Record<Corner, React.CSSProperties> = {
  nw: { left: -5, top: -5, cursor: "nwse-resize" },
  ne: { right: -5, top: -5, cursor: "nesw-resize" },
  sw: { left: -5, bottom: -5, cursor: "nesw-resize" },
  se: { right: -5, bottom: -5, cursor: "nwse-resize" },
};

/**
 * Renders an editable text field as an absolutely-positioned input overlaid
 * on the PDF page canvas. Coordinates on the wire are in PDF points; we scale
 * for display so the input tracks whatever render scale PdfViewer picked.
 */
export default function TextFieldOverlay({
  field,
  pageWidthPx,
  pageHeightPx,
  pageWidthPt,
  pageHeightPt,
  editable,
  autoFocus,
  onChange,
  onRemove,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus && inputRef.current) inputRef.current.focus();
  }, [autoFocus]);

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;

  const left = field.x * scaleX;
  const top = field.y * scaleY;
  const width = field.width * scaleX;
  const height = field.height * scaleY;
  const fontPx = field.fontSize * scaleY;

  const { bodyProps, handleProps } = useDraggableResizable({
    rect: field,
    pageWidthPt,
    pageHeightPt,
    pageWidthPx,
    pageHeightPx,
    disabled: !editable,
    onChange: (rect) => onChange({ ...field, ...rect }),
  });

  // Stop PDF click-to-place handler from firing when the user interacts with
  // the field itself (input, move grip, resize handles, or remove button).
  const swallow = (e: React.MouseEvent | React.KeyboardEvent) =>
    e.stopPropagation();

  return (
    <div
      className="absolute"
      style={{ left, top, width, height }}
      onMouseDown={swallow}
      onClick={swallow}
    >
      <input
        ref={inputRef}
        type="text"
        value={field.value}
        disabled={!editable}
        onChange={(e) => onChange({ ...field, value: e.target.value })}
        onKeyDown={swallow}
        placeholder="Type here…"
        className={`w-full h-full bg-white/95 border-2 ${
          editable ? "border-dashed border-accent" : "border-solid border-border/40"
        } rounded-sm px-1 text-black outline-none focus:bg-white focus:border-accent`}
        style={{
          fontSize: `${fontPx}px`,
          fontFamily: "Helvetica, Arial, sans-serif",
          lineHeight: 1.1,
        }}
      />
      {editable && (
        <div
          {...bodyProps}
          title="Drag to move"
          className="touch-none pointer-events-auto absolute -top-6 left-0 text-xs text-text-dim hover:text-accent bg-bg/80 px-1 rounded cursor-move select-none"
        >
          ⠿ move
        </div>
      )}
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
            onRemove(field.id);
          }}
          title="Remove text field"
          className="absolute -top-6 right-0 text-xs text-text-dim hover:text-danger bg-bg/80 px-1 rounded"
        >
          remove
        </button>
      )}
    </div>
  );
}
