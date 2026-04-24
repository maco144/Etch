// WebCrypto helpers for the send-to-sign flow (V2).
//
// The sender generates a random AES-256-GCM key in their browser, encrypts the
// PDF, uploads the ciphertext to Etch, and shares a URL whose fragment (the
// part after `#`) contains the base64url-encoded key. Browsers never send URL
// fragments in HTTP requests, so Etch literally cannot decrypt any document
// it's holding — the server only ever sees opaque bytes.
//
// AES-GCM gives us confidentiality + integrity (authentication tag) in one
// primitive. A 12-byte random IV is generated per operation and prepended to
// the ciphertext, so the ciphertext blob is self-contained: [IV (12) || CT].

import { toArrayBuffer } from "./hash";

const ALGO = "AES-GCM";
const KEY_LENGTH = 256;
const IV_LENGTH = 12; // NIST-recommended for GCM

export interface EncryptionKey {
  key: CryptoKey;
  /** Base64url-encoded raw key material, safe for URL fragment use. */
  exported: string;
}

export async function generateKey(): Promise<EncryptionKey> {
  const key = await crypto.subtle.generateKey(
    { name: ALGO, length: KEY_LENGTH },
    true,
    ["encrypt", "decrypt"],
  );
  const raw = await crypto.subtle.exportKey("raw", key);
  return { key, exported: bytesToBase64Url(new Uint8Array(raw)) };
}

export async function importKey(exported: string): Promise<CryptoKey> {
  const raw = base64UrlToBytes(exported);
  return crypto.subtle.importKey(
    "raw",
    toArrayBuffer(raw),
    { name: ALGO, length: KEY_LENGTH },
    false,
    ["encrypt", "decrypt"],
  );
}

/** Encrypt `bytes` with the given key. Output is `[IV (12) || ciphertext]`. */
export async function encrypt(bytes: Uint8Array, key: CryptoKey): Promise<Uint8Array> {
  const iv = crypto.getRandomValues(new Uint8Array(IV_LENGTH));
  const ctBuf = await crypto.subtle.encrypt(
    { name: ALGO, iv: toArrayBuffer(iv) },
    key,
    toArrayBuffer(bytes),
  );
  const ct = new Uint8Array(ctBuf);
  const out = new Uint8Array(IV_LENGTH + ct.byteLength);
  out.set(iv, 0);
  out.set(ct, IV_LENGTH);
  return out;
}

/** Decrypt a `[IV (12) || ciphertext]` blob produced by `encrypt`. */
export async function decrypt(blob: Uint8Array, key: CryptoKey): Promise<Uint8Array> {
  if (blob.byteLength <= IV_LENGTH) {
    throw new Error("ciphertext too short to contain an IV");
  }
  const iv = blob.slice(0, IV_LENGTH);
  const ct = blob.slice(IV_LENGTH);
  const pt = await crypto.subtle.decrypt(
    { name: ALGO, iv: toArrayBuffer(iv) },
    key,
    toArrayBuffer(ct),
  );
  return new Uint8Array(pt);
}

// ---------------------------------------------------------------------------
// base64url helpers (URL-safe, no padding)
// ---------------------------------------------------------------------------

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlToBytes(str: string): Uint8Array {
  const pad = "=".repeat((4 - (str.length % 4)) % 4);
  const normal = (str + pad).replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(normal);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

// ---------------------------------------------------------------------------
// Fragment-key URL helpers
//
// The fragment carries both the decryption key and the write-back capability
// token as a single URL-safe bundle (`#key=K&wt=T`). Browsers never transmit
// fragments in HTTP requests, so neither ever reaches the server. Bundling
// them means "has-the-link" implies both "can-decrypt" and "can-write-signed-
// copy-back" — which matches the product flow: the recipient needs both.
// ---------------------------------------------------------------------------

function readFragmentParams(): URLSearchParams | null {
  const hash = window.location.hash;
  if (!hash.startsWith("#")) return null;
  return new URLSearchParams(hash.slice(1));
}

/** Read the encryption key from ``location.hash`` (``#key=...``), if present. */
export function readKeyFromFragment(): string | null {
  return readFragmentParams()?.get("key") ?? null;
}

/** Read the write-back capability token (``#wt=...``), if present. */
export function readWriteTokenFromFragment(): string | null {
  return readFragmentParams()?.get("wt") ?? null;
}

/** Build a sign-via-link URL carrying the key and write token in the fragment. */
export function buildSignLink(
  documentId: string,
  exportedKey: string,
  writeToken: string,
): string {
  // base64url uses [A-Za-z0-9_-], all of which pass through encodeURIComponent
  // unchanged, so URLSearchParams leaves these values alone.
  const frag = new URLSearchParams({ key: exportedKey, wt: writeToken }).toString();
  return `${window.location.origin}/sign/${encodeURIComponent(documentId)}#${frag}`;
}
