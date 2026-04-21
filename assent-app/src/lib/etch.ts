// Thin client for the anonymous Etch Assent endpoints. Every call is a POST
// or GET against the /v1/assent/* surface exposed by etch/assent_api.py.

export type AssentEventType =
  | "created"
  | "field_added"
  | "signed"
  | "countersigned"
  | "finalized";

export interface SignerInfo {
  method: "webauthn" | "drawn";
  credential_id?: string;
  attestation?: string;
  email?: string;
  name?: string;
}

export interface FieldLocation {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface AssentEventPayload {
  kind: "assent.event";
  schema_version: 1;
  document_id: string;
  event_type: AssentEventType;
  document_hash: string;
  parent_hash: string | null;
  event_index: number;
  signer?: SignerInfo;
  location?: FieldLocation;
  timestamp?: string;
  client_metadata?: Record<string, unknown>;
}

export interface AssentReceipt {
  id: string;
  object: "assent.receipt";
  document_id: string;
  event_type: AssentEventType;
  event_index: number;
  document_hash: string;
  parent_hash: string | null;
  leaf_hash: string;
  mmr_root: string;
  chain_position: number;
  namespace: string;
  server_timestamp: number;
  client_timestamp: string | null;
  verification_url: string;
  signer?: SignerInfo | null;
  location?: FieldLocation | null;
}

export interface AssentChainResponse {
  object: "assent.chain";
  document_id: string;
  namespace: string;
  events: AssentReceipt[];
  event_count: number;
  chain_intact: boolean;
}

export interface AssentVerifyResponse {
  object: "assent.verify";
  hash: string;
  match_count: number;
  document_ids: string[];
  events: AssentReceipt[];
}

export interface AssentInclusionProof {
  object: "inclusion_proof";
  record_id: string;
  leaf_index: number;
  leaf_hash: string;
  mmr_root: string;
  prev_root: string;
  payload_hash: string;
  timestamp: number;
  algorithm: "sha256";
  verification_steps: string[];
}

// API base URL — same origin by default (Caddy proxies /v1/* to the Etch API),
// overridable via ?api= query string for local testing against a remote server.
const DEFAULT_API_BASE = "";

function apiBase(): string {
  const override = new URLSearchParams(window.location.search).get("api");
  return override ?? DEFAULT_API_BASE;
}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // not json — use status line
    }
    throw new EtchApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export class EtchApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "EtchApiError";
  }
}

export async function stampEvent(
  payload: AssentEventPayload,
): Promise<AssentReceipt> {
  const res = await fetch(`${apiBase()}/v1/assent/stamp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return asJson<AssentReceipt>(res);
}

export async function fetchDocumentChain(
  documentId: string,
): Promise<AssentChainResponse> {
  const res = await fetch(
    `${apiBase()}/v1/assent/chain/${encodeURIComponent(documentId)}`,
  );
  return asJson<AssentChainResponse>(res);
}

export async function fetchRecord(recordId: string): Promise<AssentReceipt> {
  const res = await fetch(
    `${apiBase()}/v1/assent/records/${encodeURIComponent(recordId)}`,
  );
  return asJson<AssentReceipt>(res);
}

export async function fetchProof(recordId: string): Promise<AssentInclusionProof> {
  const res = await fetch(
    `${apiBase()}/v1/assent/records/${encodeURIComponent(recordId)}/proof`,
  );
  return asJson<AssentInclusionProof>(res);
}

/** Recipient-side: given a PDF hash, find every matching event in the public
 *  chain. Throws EtchApiError(404) when the PDF isn't in Etch at all — the UI
 *  uses that to render the anti-tamper "not verified" banner. */
export async function verifyByHash(
  hashHex: string,
): Promise<AssentVerifyResponse> {
  const res = await fetch(
    `${apiBase()}/v1/assent/verify?hash=${encodeURIComponent(hashHex)}`,
  );
  return asJson<AssentVerifyResponse>(res);
}

// Resolve a URL token that might be either a record_id (rec_...) or a
// document_id (doc_...). Returns the canonical chain response either way.
export async function resolveVerify(
  token: string,
): Promise<AssentChainResponse> {
  if (token.startsWith("doc_")) {
    return fetchDocumentChain(token);
  }
  if (token.startsWith("rec_")) {
    const record = await fetchRecord(token);
    return fetchDocumentChain(record.document_id);
  }
  throw new EtchApiError(
    "Unrecognized identifier — expected rec_... or doc_...",
    400,
  );
}
