import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { uploadDocument } from "../lib/etch";
import { toArrayBuffer } from "../lib/hash";
import { buildSignLink, encrypt, generateKey } from "../lib/crypto";

const MAX_PLAINTEXT_BYTES = 10 * 1024 * 1024;

type State =
  | { kind: "idle" }
  | { kind: "encrypting"; filename: string }
  | { kind: "uploading"; filename: string }
  | { kind: "ready"; filename: string; link: string; documentId: string }
  | { kind: "error"; message: string };

/**
 * Sender flow (V2 slice 1): drop a PDF, encrypt it in-browser, upload the
 * ciphertext to Etch, and get back a shareable link whose fragment carries
 * the decryption key. The recipient opens the link, decrypts in their own
 * browser, signs, and re-uploads the signed ciphertext.
 *
 * No email yet — this slice just proves the E2EE pipe works. Slice 2 adds
 * Resend so senders can send a real email instead of copying a link.
 */
export default function Send() {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(async (file: File) => {
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setState({ kind: "error", message: "Only PDFs are supported." });
      return;
    }
    if (file.size > MAX_PLAINTEXT_BYTES) {
      setState({
        kind: "error",
        message: `PDF is ${(file.size / 1024 / 1024).toFixed(1)} MB — limit is 10 MB.`,
      });
      return;
    }

    try {
      setState({ kind: "encrypting", filename: file.name });
      const plaintext = new Uint8Array(await file.arrayBuffer());
      const { key, exported } = await generateKey();
      const ciphertext = await encrypt(plaintext, key);

      setState({ kind: "uploading", filename: file.name });
      const { document_id } = await uploadDocument(toArrayBuffer(ciphertext));

      const link = buildSignLink(document_id, exported);
      setState({ kind: "ready", filename: file.name, link, documentId: document_id });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Encryption or upload failed.";
      setState({ kind: "error", message });
    }
  }, []);

  return (
    <div className="max-w-3xl mx-auto px-6 py-16 space-y-8">
      <div className="text-center">
        <div className="text-xs uppercase tracking-wider text-text-muted mb-2">
          Etch Assent — Send
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tight mb-3">
          Send a PDF for signature
        </h1>
        <p className="text-text-dim max-w-xl mx-auto">
          Encrypted in your browser with AES-256-GCM. Etch stores the ciphertext;
          the decryption key travels only in the URL fragment, which browsers
          never send to servers. Even we can't read it.
        </p>
      </div>

      {(state.kind === "idle" || state.kind === "error") && (
        <DropZone
          dragging={dragging}
          setDragging={setDragging}
          onFile={handleFile}
          inputRef={inputRef}
        />
      )}

      {state.kind === "error" && (
        <div className="text-sm text-danger text-center">{state.message}</div>
      )}

      {(state.kind === "encrypting" || state.kind === "uploading") && (
        <div className="card p-8 text-center text-sm text-text-dim">
          {state.kind === "encrypting"
            ? `Encrypting ${state.filename}…`
            : `Uploading ciphertext for ${state.filename}…`}
        </div>
      )}

      {state.kind === "ready" && <ReadyPanel state={state} onReset={() => setState({ kind: "idle" })} />}

      <p className="text-xs text-text-muted text-center">
        This slice sends no email. Copy the link and share it yourself — email
        integration (via Resend) lands in the next release.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------

interface DropZoneProps {
  dragging: boolean;
  setDragging: (v: boolean) => void;
  onFile: (f: File) => void;
  inputRef: React.RefObject<HTMLInputElement>;
}

function DropZone({ dragging, setDragging, onFile, inputRef }: DropZoneProps) {
  return (
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
        if (file) onFile(file);
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
      <div className="text-lg font-medium">Drop the PDF to send</div>
      <div className="text-sm text-text-dim mt-1">or click to browse · Max 10 MB</div>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
        }}
      />
    </div>
  );
}

function ReadyPanel({
  state,
  onReset,
}: {
  state: Extract<State, { kind: "ready" }>;
  onReset: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(state.link);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="card p-6 space-y-5">
      <div>
        <div className="chip-success inline-block mb-2">Ready to share</div>
        <div className="text-sm text-text-dim">
          <span className="font-medium text-text">{state.filename}</span> is encrypted and
          stored. Send this link to whoever needs to sign:
        </div>
      </div>

      <div className="bg-elevated rounded-lg p-3 font-mono text-xs break-all select-all">
        {state.link}
      </div>

      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={copy} className="btn-primary">
          {copied ? "Copied" : "Copy link"}
        </button>
        <Link to={state.link.replace(window.location.origin, "")} className="btn-secondary">
          Open sign page
        </Link>
        <button type="button" onClick={onReset} className="btn-secondary">
          Send another
        </button>
      </div>

      <div className="text-xs text-text-muted">
        Document ID <span className="font-mono">{state.documentId}</span>. The key after{" "}
        <span className="font-mono">#</span> is only in your clipboard — we never received
        it. Anyone with this full link can decrypt and sign; anyone with just the
        document ID cannot.
      </div>
    </div>
  );
}
