// SHA-256 helpers built on WebCrypto. No dependencies, no polyfills.
//
// The whole design of Assent assumes PDFs never leave the browser — so every
// hash we compute here must match what Etch stores. We use plain hex SHA-256
// of the raw bytes, matching the `document_hash` format the backend expects.

export async function sha256(input: Uint8Array | ArrayBuffer): Promise<string> {
  // TS 5.7+ narrows crypto.subtle.digest's BufferSource to ArrayBufferView<ArrayBuffer>,
  // which excludes a plain Uint8Array<ArrayBufferLike>. Copy into a fresh AB.
  const buffer = input instanceof Uint8Array ? toArrayBuffer(input) : input;
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return bufferToHex(digest);
}

export async function sha256OfFile(file: File | Blob): Promise<string> {
  const buf = await file.arrayBuffer();
  return sha256(buf);
}

export async function sha256OfString(value: string): Promise<string> {
  return sha256(new TextEncoder().encode(value));
}

export function bufferToHex(buf: ArrayBuffer | Uint8Array): string {
  const view = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < view.length; i++) {
    out += view[i].toString(16).padStart(2, "0");
  }
  return out;
}

export function bufferToBase64(buf: ArrayBuffer | Uint8Array): string {
  const view = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let binary = "";
  for (let i = 0; i < view.byteLength; i++) {
    binary += String.fromCharCode(view[i]);
  }
  return btoa(binary);
}

export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.length % 2 === 0 ? hex : "0" + hex;
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.substr(i * 2, 2), 16);
  }
  return out;
}

/**
 * Materialize a Uint8Array into a freshly-allocated ArrayBuffer. TypeScript
 * 5.7+ parameterizes ArrayBufferView so generic Uint8Array<ArrayBufferLike>
 * (which could be SharedArrayBuffer-backed) no longer satisfies APIs like
 * Blob, crypto.subtle, or WebAuthn challenge. We copy once; at our sizes
 * (document hashes, PDF bytes up to 10 MB) the cost is trivial.
 */
export function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const out = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(out).set(bytes);
  return out;
}

// Generate a stable client-side document ID. Not cryptographic; just a
// collision-resistant identifier that ties events together on our chain.
export function newDocumentId(): string {
  const random = crypto.getRandomValues(new Uint8Array(12));
  const b64 = bufferToBase64(random).replace(/[+/]/g, "").replace(/=+$/, "");
  return `doc_${b64.slice(0, 16)}`;
}
