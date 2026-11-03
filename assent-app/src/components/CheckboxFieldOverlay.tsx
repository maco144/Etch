import type { CheckboxFieldValue } from "../lib/pdf";
import { useDraggableResizable, type Corner } from "../hooks/useDraggableResizable";

interface Props {
  field: CheckboxFieldValue;
  pageWidthPx: number;
  pageHeightPx: number;
  pageWidthPt: number;
  pageHeightPt: number;
  editable: boolean;
  onToggle: (id: string) => void;
  onChange: (next: CheckboxFieldValue) => void;
  onRemove: (id: string) => void;
}

const MIN_PT = 6;

/**
 * A checkbox (or radio option) over the PDF. Click toggles; the body drags,
 * the SE corner resizes. The check mark is drawn the same way the flattener
 * draws it, so what you see is what gets baked in.
 */
export default function CheckboxFieldOverlay({
  field,
  pageWidthPx,
  pageHeightPx,
  pageWidthPt,
  pageHeightPt,
  editable,
  onToggle,
  onChange,
  onRemove,
}: Props) {
  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;
  const left = field.x * scaleX;
  const top = field.y * scaleY;
  const width = field.width * scaleX;
  const height = field.height * scaleY;
  const radio = field.radioValue !== undefined;

  const { bodyProps, handleProps } = useDraggableResizable({
    rect: field,
    pageWidthPt,
    pageHeightPt,
    pageWidthPx,
    pageHeightPx,
    minWidthPt: MIN_PT,
    minHeightPt: MIN_PT,
    disabled: !editable,
    onChange: (rect) => onChange({ ...field, ...rect }),
    onClick: () => onToggle(field.id),
  });

  const corner: Corner = "se";

  return (
    <div
      {...bodyProps}
      onClick={(e) => e.stopPropagation()}
      role={radio ? "radio" : "checkbox"}
      aria-checked={field.checked}
      aria-label={field.label ?? (radio ? "Option" : "Checkbox")}
      title={field.label}
      className={`group absolute touch-none bg-white/90 border-2 ${radio ? "rounded-full" : "rounded-[2px]"} ${
        editable ? "border-dashed border-accent cursor-pointer pointer-events-auto" : "border-solid border-border/40 pointer-events-none"
      }`}
      style={{ left, top, width, height }}
    >
      {field.checked && (
        <svg viewBox="0 0 10 10" className="absolute inset-0 w-full h-full pointer-events-none" aria-hidden>
          {radio ? (
            <circle cx="5" cy="5" r="2.8" fill="#0d0d1a" />
          ) : (
            <path d="M2 5 L4.2 7.5 L8.2 2" fill="none" stroke="#0d0d1a" strokeWidth="1.2" strokeLinecap="round" />
          )}
        </svg>
      )}
      {editable && (
        <>
          <div
            {...handleProps(corner)}
            className="absolute -right-1.5 -bottom-1.5 w-2 h-2 bg-accent border border-white rounded-sm pointer-events-auto touch-none cursor-nwse-resize opacity-0 group-hover:opacity-100"
          />
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove(field.id);
            }}
            title="Remove checkbox"
            className="absolute -top-4 -right-3 text-[10px] leading-none text-text-dim hover:text-danger bg-bg/80 px-0.5 rounded opacity-0 group-hover:opacity-100 pointer-events-auto"
          >
            ×
          </button>
        </>
      )}
    </div>
  );
}
