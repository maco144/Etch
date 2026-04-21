import { useEffect, useRef, useState } from "react";

interface Props {
  onSubmit: (pngDataUrl: string, widthPx: number, heightPx: number) => void;
  onCancel: () => void;
}

/**
 * Pointer-event-based signature pad. Supports mouse, touch, and pen. Outputs
 * a PNG data URL sized 600x200 — we keep it modest so the flatten step doesn't
 * bloat the PDF.
 */
export default function SignaturePad({ onSubmit, onCancel }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);
  const [hasStroke, setHasStroke] = useState(false);

  const width = 600;
  const height = 200;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#0a0a0f";
    ctx.lineWidth = 2.25;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
  }, []);

  const localPoint = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = e.currentTarget;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    };
  };

  const start = (e: React.PointerEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    (e.currentTarget as HTMLCanvasElement).setPointerCapture(e.pointerId);
    drawing.current = true;
    last.current = localPoint(e);
  };

  const move = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawing.current) return;
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx || !last.current) return;
    const p = localPoint(e);
    ctx.beginPath();
    ctx.moveTo(last.current.x, last.current.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    last.current = p;
    if (!hasStroke) setHasStroke(true);
  };

  const end = () => {
    drawing.current = false;
    last.current = null;
  };

  const clear = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    setHasStroke(false);
  };

  const submit = () => {
    const canvas = canvasRef.current;
    if (!canvas || !hasStroke) return;
    onSubmit(canvas.toDataURL("image/png"), width, height);
  };

  return (
    <div className="card p-5">
      <div className="flex justify-between items-center mb-3">
        <div>
          <div className="text-sm font-medium">Draw your signature</div>
          <div className="text-xs text-text-muted">
            Signs using ESIGN/UETA "drawn" mode. For cryptographic non-repudiation,
            cancel and choose Passkey instead.
          </div>
        </div>
        <button
          type="button"
          onClick={clear}
          className="text-xs text-text-dim hover:text-text"
        >
          clear
        </button>
      </div>
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className="w-full bg-white rounded-md border border-border touch-none cursor-crosshair"
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={end}
        onPointerLeave={end}
        onPointerCancel={end}
      />
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="btn-secondary">
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!hasStroke}
          className="btn-primary disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Use this signature
        </button>
      </div>
    </div>
  );
}
