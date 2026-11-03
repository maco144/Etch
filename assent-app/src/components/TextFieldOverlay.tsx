import { useEffect, useRef } from "react";
import {
  TEXT_FIELD_BORDER_PX,
  TEXT_FIELD_INSET_PT,
  TEXT_FIELD_LINE_HEIGHT,
  fitTextBox,
  type TextFieldValue,
} from "../lib/pdf";
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

  // Auto-fit: the box hugs the text as you type, so it never covers more of
  // the page than the text itself will. The left edge stays put; width tracks
  // the value (trailing spaces included while typing, so the caret never
  // scrolls out of view) and is clamped to the page edge.
  const withValue = (value: string): TextFieldValue => {
    if (!field.autoFit) return { ...field, value };
    const { width } = fitTextBox(value, field.fontSize, scaleX, scaleY);
    return { ...field, value, width: Math.min(width, pageWidthPt - field.x) };
  };

  // After a manual resize: snap the box back to the text in both directions
  // and turn auto-fit back on.
  // Trailing spaces draw nothing in the PDF, so they're dropped — the box
  // ends at the last glyph. Vertical center stays put.
  const fit = () => {
    const text = field.value.trimEnd();
    const { width, height } = fitTextBox(text, field.fontSize, scaleX, scaleY);
    const cy = field.y + field.height / 2;
    const y = Math.max(0, Math.min(pageHeightPt - height, cy - height / 2));
    onChange({ ...field, value: text, autoFit: true, y, width: Math.min(width, pageWidthPt - field.x), height });
    if (inputRef.current) inputRef.current.scrollLeft = 0;
  };

  const { bodyProps, handleProps } = useDraggableResizable({
    rect: field,
    // Text fields get trimmed tight, so allow far below the signature-sized
    // defaults: one glyph wide, one line tall.
    minWidthPt: field.fontSize,
    minHeightPt: field.fontSize,
    pageWidthPt,
    pageHeightPt,
    pageWidthPx,
    pageHeightPx,
    disabled: !editable,
    // A move keeps the size; a resize means the user wants this exact box,
    // so auto-fit stops fighting them until they press "fit" again.
    onChange: (rect) =>
      onChange({
        ...field,
        ...rect,
        autoFit: field.autoFit && rect.width === field.width && rect.height === field.height,
      }),
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
        onChange={(e) => onChange(withValue(e.target.value))}
        // Leaving the field trims trailing spaces off an auto-fit box. Width
        // only, so nothing under the pointer shifts on the way to a click.
        onBlur={() => {
          if (field.autoFit && field.value !== field.value.trimEnd()) onChange(withValue(field.value.trimEnd()));
        }}
        onKeyDown={swallow}
        placeholder="Type here…"
        className={`w-full h-full bg-white/95 border-2 ${
          editable ? "border-dashed border-accent" : "border-solid border-border/40"
        } rounded-sm text-black outline-none focus:bg-white focus:border-accent`}
        style={{
          fontSize: `${fontPx}px`,
          fontFamily: "Helvetica, Arial, sans-serif",
          lineHeight: TEXT_FIELD_LINE_HEIGHT,
          // Same inset the PDF text is drawn at, so the overlay is a preview
          // of the output and a fitted box doesn't clip the last glyph.
          padding: `0 ${Math.max(0, TEXT_FIELD_INSET_PT * scaleX - TEXT_FIELD_BORDER_PX)}px`,
        }}
      />
      {editable && (
        <div className="absolute -top-6 left-0 flex gap-1 whitespace-nowrap text-xs">
          <div
            {...bodyProps}
            title="Drag to move"
            className="touch-none pointer-events-auto text-text-dim hover:text-accent bg-bg/80 px-1 rounded cursor-move select-none"
          >
            ⠿ move
          </div>
          {!field.autoFit && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                fit();
              }}
              title="Snap the box back to the text"
              className="text-text-dim hover:text-accent bg-bg/80 px-1 rounded"
            >
              fit
            </button>
          )}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove(field.id);
            }}
            title="Remove text field"
            className="text-text-dim hover:text-danger bg-bg/80 px-1 rounded"
          >
            remove
          </button>
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
    </div>
  );
}
