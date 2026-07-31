import { useCallback, useRef } from "react";

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type Corner = "nw" | "ne" | "sw" | "se";

export interface UseDraggableResizableArgs {
  rect: Rect;
  pageWidthPt: number;
  pageHeightPt: number;
  pageWidthPx: number;
  pageHeightPx: number;
  minWidthPt?: number;
  minHeightPt?: number;
  disabled?: boolean;
  onChange: (next: Rect) => void;
  onClick?: () => void;
}

const DEFAULT_MIN_WIDTH_PT = 40;
const DEFAULT_MIN_HEIGHT_PT = 20;
const CLICK_THRESHOLD_PX = 4;

interface DragState {
  mode: "move" | "resize";
  corner?: Corner;
  startClientX: number;
  startClientY: number;
  startRect: Rect;
  moved: boolean;
}

/**
 * Pointer-drag move + 4-corner resize for a field overlay, in PDF-point
 * space. Pointer deltas arrive in screen px; page pixel/point sizes convert
 * them to pt so callers only ever see/set positions in the wire format.
 */
export function useDraggableResizable({
  rect,
  pageWidthPt,
  pageHeightPt,
  pageWidthPx,
  pageHeightPx,
  minWidthPt = DEFAULT_MIN_WIDTH_PT,
  minHeightPt = DEFAULT_MIN_HEIGHT_PT,
  disabled = false,
  onChange,
  onClick,
}: UseDraggableResizableArgs) {
  const dragState = useRef<DragState | null>(null);

  const scaleX = pageWidthPx / pageWidthPt;
  const scaleY = pageHeightPx / pageHeightPt;

  const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max);

  const beginMove = useCallback(
    (e: React.PointerEvent) => {
      if (disabled) return;
      // preventDefault on pointerdown suppresses the browser's compatibility
      // mousedown/mouseup/click events for this interaction (same reasoning
      // as SignaturePad.tsx's existing pointerdown handler) — without it, a
      // click-without-movement still emits a native "click" after pointerup
      // that would bubble past this element to the page's place-a-field
      // handler and add a duplicate field on top of the one just clicked.
      e.preventDefault();
      e.stopPropagation();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragState.current = {
        mode: "move",
        startClientX: e.clientX,
        startClientY: e.clientY,
        startRect: rect,
        moved: false,
      };
    },
    [disabled, rect],
  );

  const beginResize = useCallback(
    (corner: Corner) => (e: React.PointerEvent) => {
      if (disabled) return;
      e.preventDefault();
      e.stopPropagation();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragState.current = {
        mode: "resize",
        corner,
        startClientX: e.clientX,
        startClientY: e.clientY,
        startRect: rect,
        moved: false,
      };
    },
    [disabled, rect],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const drag = dragState.current;
      if (!drag) return;
      const deltaXpx = e.clientX - drag.startClientX;
      const deltaYpx = e.clientY - drag.startClientY;
      if (Math.abs(deltaXpx) > CLICK_THRESHOLD_PX || Math.abs(deltaYpx) > CLICK_THRESHOLD_PX) {
        drag.moved = true;
      }
      const deltaXpt = deltaXpx / scaleX;
      const deltaYpt = deltaYpx / scaleY;
      const { startRect } = drag;

      if (drag.mode === "move") {
        const x = clamp(startRect.x + deltaXpt, 0, pageWidthPt - startRect.width);
        const y = clamp(startRect.y + deltaYpt, 0, pageHeightPt - startRect.height);
        onChange({ ...startRect, x, y });
        return;
      }

      const left0 = startRect.x;
      const top0 = startRect.y;
      const right0 = startRect.x + startRect.width;
      const bottom0 = startRect.y + startRect.height;

      switch (drag.corner) {
        case "nw": {
          const left = clamp(left0 + deltaXpt, 0, right0 - minWidthPt);
          const top = clamp(top0 + deltaYpt, 0, bottom0 - minHeightPt);
          onChange({ x: left, y: top, width: right0 - left, height: bottom0 - top });
          break;
        }
        case "ne": {
          const right = clamp(right0 + deltaXpt, left0 + minWidthPt, pageWidthPt);
          const top = clamp(top0 + deltaYpt, 0, bottom0 - minHeightPt);
          onChange({ x: left0, y: top, width: right - left0, height: bottom0 - top });
          break;
        }
        case "sw": {
          const left = clamp(left0 + deltaXpt, 0, right0 - minWidthPt);
          const bottom = clamp(bottom0 + deltaYpt, top0 + minHeightPt, pageHeightPt);
          onChange({ x: left, y: top0, width: right0 - left, height: bottom - top0 });
          break;
        }
        case "se": {
          const right = clamp(right0 + deltaXpt, left0 + minWidthPt, pageWidthPt);
          const bottom = clamp(bottom0 + deltaYpt, top0 + minHeightPt, pageHeightPt);
          onChange({ x: left0, y: top0, width: right - left0, height: bottom - top0 });
          break;
        }
      }
    },
    [scaleX, scaleY, pageWidthPt, pageHeightPt, minWidthPt, minHeightPt, onChange],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      const drag = dragState.current;
      if (!drag) return;
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      if (drag.mode === "move" && !drag.moved) onClick?.();
      dragState.current = null;
    },
    [onClick],
  );

  return {
    bodyProps: disabled
      ? {}
      : { onPointerDown: beginMove, onPointerMove, onPointerUp, onPointerCancel: onPointerUp },
    handleProps: (corner: Corner) =>
      disabled
        ? {}
        : {
            onPointerDown: beginResize(corner),
            onPointerMove,
            onPointerUp,
            onPointerCancel: onPointerUp,
          },
  };
}
