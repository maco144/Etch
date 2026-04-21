import { useState } from "react";
import type { AssentChainResponse, AssentReceipt } from "../lib/etch";

interface Props {
  chain: AssentChainResponse;
  uploadedHash?: string;
}

/**
 * The single most important screen. Must read like a Stripe receipt, not a
 * blockchain explorer. The raw-chain details live behind a "Show full chain"
 * disclosure.
 */
export default function VerifyChain({ chain, uploadedHash }: Props) {
  const [showDetails, setShowDetails] = useState(false);
  const signedEvent = chain.events.find(
    (e) => e.event_type === "signed" || e.event_type === "countersigned",
  );
  const finalized = chain.events[chain.events.length - 1];

  const hashMatch = uploadedHash
    ? chain.events.some((e) => e.document_hash === uploadedHash)
    : null;

  const status = !chain.chain_intact
    ? "tampered"
    : hashMatch === false
      ? "not-found"
      : "verified";

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <StatusBanner status={status} />

      <div className="card p-6 space-y-5">
        {signedEvent && signedEvent.signer ? (
          <section>
            <div className="text-xs uppercase tracking-wider text-text-muted mb-1">
              Signed by
            </div>
            <div className="text-lg">
              {signedEvent.signer.email ??
                signedEvent.signer.name ??
                "Anonymous signer"}
            </div>
            <div className="text-sm text-text-dim">
              via{" "}
              <span className="font-medium text-text">
                {signedEvent.signer.method === "webauthn"
                  ? "WebAuthn (passkey)"
                  : "Drawn signature"}
              </span>
            </div>
            <div className="text-sm text-text-dim mt-1">
              on{" "}
              <span className="font-medium text-text">
                {formatDate(signedEvent.server_timestamp)}
              </span>
            </div>
          </section>
        ) : (
          <section className="text-sm text-text-dim">
            Chain created, no signing event yet recorded.
          </section>
        )}

        <hr className="border-border" />

        <section className="grid sm:grid-cols-2 gap-y-3 text-sm">
          <Field label="Namespace" value={chain.namespace} mono />
          <Field label="Document" value={chain.document_id} mono />
          <Field label="Events" value={String(chain.event_count)} />
          <Field
            label="Finalized"
            value={finalized ? formatDate(finalized.server_timestamp) : "—"}
          />
        </section>

        <div className="flex flex-wrap gap-3 pt-2">
          <button
            type="button"
            onClick={() => setShowDetails((s) => !s)}
            className="btn-secondary"
          >
            {showDetails ? "Hide" : "Show"} full chain
          </button>
          <a
            href={`/v1/assent/chain/${encodeURIComponent(chain.document_id)}`}
            download={`${chain.document_id}.receipt.json`}
            className="btn-secondary"
          >
            Download receipt JSON
          </a>
        </div>

        {showDetails && <ChainTable events={chain.events} />}
      </div>

      <p className="text-xs text-text-muted text-center">
        This receipt is independently verifiable offline. Anyone can download the
        receipt JSON and verify the Merkle chain without talking to Etch.
      </p>
    </div>
  );
}

function StatusBanner({
  status,
}: {
  status: "verified" | "not-found" | "tampered";
}) {
  if (status === "verified") {
    return (
      <div className="card p-5 border-success/40 bg-success/10 text-success">
        <div className="flex items-center gap-3">
          <CheckIcon />
          <div>
            <div className="text-lg font-semibold">Document verified</div>
            <div className="text-sm opacity-90">
              Hash matches the signed PDF in the Etch chain.
            </div>
          </div>
        </div>
      </div>
    );
  }
  if (status === "not-found") {
    return (
      <div className="card p-5 border-danger/40 bg-danger/10 text-danger">
        <div className="flex items-center gap-3">
          <CrossIcon />
          <div>
            <div className="text-lg font-semibold">Not verified</div>
            <div className="text-sm opacity-90">
              This document does not match any signature in the Etch chain.
              Either it has been modified after signing, or it was never signed
              with Etch Assent.
            </div>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="card p-5 border-danger/40 bg-danger/10 text-danger">
      <div className="flex items-center gap-3">
        <CrossIcon />
        <div>
          <div className="text-lg font-semibold">Chain tampering detected</div>
          <div className="text-sm opacity-90">
            Parent-hash linkage does not match. Contact the signer before
            trusting this document.
          </div>
        </div>
      </div>
    </div>
  );
}

function ChainTable({ events }: { events: AssentReceipt[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="min-w-full text-sm">
        <thead className="bg-elevated text-text-dim text-xs uppercase">
          <tr>
            <th className="text-left px-3 py-2">#</th>
            <th className="text-left px-3 py-2">Event</th>
            <th className="text-left px-3 py-2">Time</th>
            <th className="text-left px-3 py-2">Record</th>
            <th className="text-left px-3 py-2">Document hash</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="border-t border-border">
              <td className="px-3 py-2 font-mono text-text-dim">
                {e.event_index}
              </td>
              <td className="px-3 py-2">{e.event_type}</td>
              <td className="px-3 py-2 text-text-dim">
                {formatDate(e.server_timestamp)}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-text-dim">
                {e.id}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-text-dim max-w-[180px] truncate">
                {e.document_hash}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-text-muted mb-0.5">
        {label}
      </div>
      <div className={mono ? "font-mono text-xs break-all text-text" : ""}>
        {value}
      </div>
    </div>
  );
}

function formatDate(ts: number): string {
  const d = new Date(ts * 1000);
  const iso = d.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`;
}

function CheckIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" />
      <path
        d="m8 12 3 3 5-6"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" />
      <path
        d="m9 9 6 6M15 9l-6 6"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
