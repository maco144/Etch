import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import VerifyChain from "../components/VerifyChain";
import {
  EtchApiError,
  fetchDocumentChain,
  resolveVerify,
  verifyByHash,
  type AssentChainResponse,
} from "../lib/etch";
import { sha256OfFile } from "../lib/hash";

type LoadState =
  | { kind: "idle" }
  | { kind: "hashing"; filename: string }
  | { kind: "chain"; chain: AssentChainResponse; uploadedHash?: string }
  | { kind: "not-found"; hash: string }
  | { kind: "error"; message: string };

export default function Verify() {
  const { recordOrDocId } = useParams();
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);

  // When the URL has a record_id / document_id, preload the chain. Without
  // one, we stay on the upload form until the user drops a PDF.
  useEffect(() => {
    if (!recordOrDocId) {
      setState({ kind: "idle" });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const chain = await resolveVerify(recordOrDocId);
        if (!cancelled) setState({ kind: "chain", chain });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof EtchApiError && err.status === 404) {
          setState({
            kind: "error",
            message:
              "No Etch Assent chain found for this identifier. Either it was never signed with Assent, or it lives in a different Etch namespace.",
          });
        } else {
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : "Verification failed.",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [recordOrDocId]);

  const handleFile = useCallback(async (file: File) => {
    setState({ kind: "hashing", filename: file.name });
    let hash = "";
    try {
      hash = await sha256OfFile(file);
      const verified = await verifyByHash(hash);
      const primaryDoc = verified.document_ids[0];
      if (!primaryDoc) {
        setState({ kind: "not-found", hash });
        return;
      }
      const chain = await fetchDocumentChain(primaryDoc);
      setState({ kind: "chain", chain, uploadedHash: hash });
    } catch (err) {
      if (err instanceof EtchApiError && err.status === 404) {
        setState({ kind: "not-found", hash });
        return;
      }
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Verification failed.",
      });
    }
  }, []);

  // -------------------------------------------------------------------------

  if (state.kind === "chain") {
    return (
      <div className="max-w-4xl mx-auto px-6 py-10">
        <Heading subtitle={state.chain.document_id} />
        <VerifyChain chain={state.chain} uploadedHash={state.uploadedHash} />
        <UploadBox
          compact
          dragging={dragging}
          setDragging={setDragging}
          onFile={handleFile}
          inputRef={inputRef}
          dropRef={dropRef}
        />
      </div>
    );
  }

  if (state.kind === "not-found") {
    return (
      <div className="max-w-3xl mx-auto px-6 py-10 space-y-6">
        <Heading subtitle="Not verified" />
        <div className="card p-6 border-danger/40 bg-danger/10 text-danger">
          <div className="text-lg font-semibold mb-1">Not verified</div>
          <div className="text-sm opacity-90">
            This document does not match any signature in the Etch chain.
            Either it has been modified after signing, or it was never signed
            with Etch Assent.
          </div>
          {state.hash && (
            <div className="mt-3 text-xs hash opacity-80">
              hashed: {state.hash}
            </div>
          )}
        </div>
        <UploadBox
          dragging={dragging}
          setDragging={setDragging}
          onFile={handleFile}
          inputRef={inputRef}
          dropRef={dropRef}
        />
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="max-w-3xl mx-auto px-6 py-10 space-y-6">
        <Heading subtitle={recordOrDocId ?? "Verify"} />
        <div className="card p-6 text-danger text-sm">{state.message}</div>
        <UploadBox
          dragging={dragging}
          setDragging={setDragging}
          onFile={handleFile}
          inputRef={inputRef}
          dropRef={dropRef}
        />
      </div>
    );
  }

  if (state.kind === "hashing") {
    return (
      <div className="max-w-3xl mx-auto px-6 py-16 text-center text-text-dim">
        Hashing {state.filename}…
      </div>
    );
  }

  // Idle — bare /verify route. Just the upload box.
  return (
    <div className="max-w-3xl mx-auto px-6 py-16 space-y-6">
      <Heading subtitle="Upload a PDF to verify" />
      <p className="text-center text-sm text-text-dim max-w-lg mx-auto">
        Your PDF is hashed in the browser and compared against the public Etch
        Assent chain. The file itself is never uploaded.
      </p>
      <UploadBox
        dragging={dragging}
        setDragging={setDragging}
        onFile={handleFile}
        inputRef={inputRef}
        dropRef={dropRef}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------

function Heading({ subtitle }: { subtitle: string }) {
  return (
    <div className="text-center mb-6">
      <div className="text-xs uppercase tracking-wider text-text-muted mb-2">
        Etch Assent — Verify
      </div>
      <h1 className="text-2xl sm:text-3xl font-semibold">
        <span className="font-mono text-text-dim break-all">{subtitle}</span>
      </h1>
    </div>
  );
}

interface UploadBoxProps {
  dragging: boolean;
  setDragging: (v: boolean) => void;
  onFile: (f: File) => void;
  inputRef: React.RefObject<HTMLInputElement>;
  dropRef: React.RefObject<HTMLDivElement>;
  compact?: boolean;
}

function UploadBox({
  dragging,
  setDragging,
  onFile,
  inputRef,
  dropRef,
  compact,
}: UploadBoxProps) {
  return (
    <div
      ref={dropRef}
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
      className={`card cursor-pointer border-dashed text-center transition-colors ${
        compact ? "p-6 mt-10" : "p-12"
      } ${dragging ? "border-accent bg-accent/5" : "hover:bg-surface/50"}`}
    >
      <div className="text-sm font-medium">
        {compact
          ? "Check another PDF against this chain"
          : "Drop the signed PDF here"}
      </div>
      <div className="text-xs text-text-dim mt-1">
        Hashed locally · never uploaded
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
        }}
      />
    </div>
  );
}

