import { useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { stagePending } from "../lib/handoff";
import { pathFor } from "../lib/routing";

const MAX_BYTES = 10 * 1024 * 1024;

/**
 * Landing page: one big drop zone. Copy matches the Etch marketing site's
 * voice ("permanent proof of agreement — no vendor to trust").
 */
export default function Home() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const handleFile = useCallback(
    async (file: File) => {
      setError(null);
      if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
        setError("Only PDF files are supported in V1.");
        return;
      }
      if (file.size > MAX_BYTES) {
        setError(`This PDF is ${formatSize(file.size)} — max 10 MB in V1.`);
        return;
      }
      const bytes = new Uint8Array(await file.arrayBuffer());
      const token = stagePending({ filename: file.name, bytes });
      navigate(`${pathFor("sign")}?p=${token}`);
    },
    [navigate],
  );

  return (
    <div className="max-w-4xl mx-auto px-6 py-16">
      <div className="text-center mb-10">
        <h1 className="text-4xl sm:text-5xl font-bold tracking-tight mb-4">
          Permanent proof of agreement.
          <br />
          <span className="text-text-dim">No vendor to trust.</span>
        </h1>
        <p className="text-text-dim max-w-xl mx-auto">
          Drop a PDF. Sign it in your browser. Get a receipt anyone can verify,
          forever — even if we disappear. Your document never leaves your device.
        </p>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void handleFile(file);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        className={`card cursor-pointer border-dashed text-center p-16 transition-colors ${
          dragging ? "border-accent bg-accent/5" : "hover:bg-surface/50"
        }`}
      >
        <div className="text-5xl mb-4" aria-hidden>
          ⬆
        </div>
        <div className="text-lg font-medium">Drop a PDF here</div>
        <div className="text-sm text-text-dim mt-1">
          or click to browse. Max 10 MB.
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
      </div>

      {error && (
        <div className="mt-4 text-sm text-danger text-center">{error}</div>
      )}

      <div className="mt-6 text-center text-sm text-text-dim">
        Need someone else to sign?{" "}
        <Link to={pathFor("send")} className="text-accent hover:underline">
          Send an encrypted link →
        </Link>
      </div>

      <section className="mt-20 grid sm:grid-cols-3 gap-6">
        <Feature
          title="Never uploaded"
          body="The PDF is hashed and signed entirely in your browser. Only the hash reaches Etch."
        />
        <Feature
          title="Independently verifiable"
          body="Anyone with the receipt can audit the chain offline — no Etch account, no API key required."
        />
        <Feature
          title="Passkey-signed"
          body="Pick Touch ID, Windows Hello, or a YubiKey. Hardware-backed non-repudiation out of the box."
        />
      </section>

      <p className="mt-16 text-xs text-text-muted text-center">
        Built on Etch — the same tamper-evident Merkle chain used by the SoR API at{" "}
        <a href="https://etch.locker" className="hover:underline">
          etch.locker
        </a>
        . ESIGN / UETA compatible. eIDAS "Advanced" when using Passkey mode.
      </p>
    </div>
  );
}

function Feature({ title, body }: { title: string; body: string }) {
  return (
    <div className="card p-5">
      <div className="text-sm font-semibold mb-1">{title}</div>
      <div className="text-sm text-text-dim leading-relaxed">{body}</div>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
