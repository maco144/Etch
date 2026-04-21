// Signature strategies for Assent. Two modes:
//   draw:     capture a trace on a canvas → PNG data URL
//   webauthn: sign the PDF hash with a platform authenticator (Touch ID etc.)
//
// Drawn signatures are good enough for ESIGN/UETA. WebAuthn adds a hardware-
// backed non-repudiation layer — the private key never leaves the authenticator,
// so we can prove a specific credential signed this exact document hash.

import { bufferToBase64, hexToBytes, toArrayBuffer } from "./hash";

export type SignatureMode = "drawn" | "webauthn";

export interface DrawnSignature {
  mode: "drawn";
  pngDataUrl: string;
  widthPx: number;
  heightPx: number;
}

export interface WebAuthnSignature {
  mode: "webauthn";
  credentialId: string;
  attestation?: string;
  clientDataJson?: string;
  authenticatorData?: string;
  signature?: string;
  pngDataUrl: string; // Visual "Signed by passkey" stamp for the flatten step.
  widthPx: number;
  heightPx: number;
}

export type CapturedSignature = DrawnSignature | WebAuthnSignature;

// ---------------------------------------------------------------------------
// WebAuthn
// ---------------------------------------------------------------------------

const RP_NAME = "Etch Assent";

/**
 * Create or re-use a WebAuthn credential, then produce an assertion over the
 * document hash. The assertion signs a challenge = hashBytes, so the resulting
 * signature binds a specific authenticator to this specific PDF.
 *
 * Returns everything we need to embed in the Etch record.
 */
export async function signWithPasskey(args: {
  documentHashHex: string;
  userEmail?: string;
  userName?: string;
  existingCredentialIdB64?: string;
}): Promise<WebAuthnSignature> {
  if (!window.PublicKeyCredential) {
    throw new Error("WebAuthn is not supported in this browser");
  }

  // WebAuthn's challenge is typed as ArrayBufferView<ArrayBuffer> in TS 5.7+,
  // so we hand it a fresh ArrayBuffer rather than a plain Uint8Array view.
  const challenge = toArrayBuffer(hexToBytes(args.documentHashHex));
  const rpId = window.location.hostname;

  let credentialIdB64 = args.existingCredentialIdB64;

  if (!credentialIdB64) {
    const userId = crypto.getRandomValues(new Uint8Array(16));
    const created = (await navigator.credentials.create({
      publicKey: {
        challenge,
        rp: { id: rpId, name: RP_NAME },
        user: {
          id: userId,
          name: args.userEmail ?? "signer",
          displayName: args.userName ?? "Signer",
        },
        pubKeyCredParams: [
          { type: "public-key", alg: -7 }, // ES256
          { type: "public-key", alg: -257 }, // RS256
        ],
        authenticatorSelection: {
          userVerification: "preferred",
          residentKey: "preferred",
        },
        timeout: 60_000,
        attestation: "none",
      },
    })) as PublicKeyCredential | null;

    if (!created) throw new Error("Passkey enrollment cancelled");
    credentialIdB64 = bufferToBase64(new Uint8Array(created.rawId));
  }

  // Always ask the authenticator to sign this hash as the assertion challenge.
  const assertion = (await navigator.credentials.get({
    publicKey: {
      challenge,
      rpId,
      allowCredentials: [
        {
          id: toArrayBuffer(base64ToBytes(credentialIdB64)),
          type: "public-key",
        },
      ],
      userVerification: "preferred",
      timeout: 60_000,
    },
  })) as PublicKeyCredential | null;

  if (!assertion) throw new Error("Passkey signature cancelled");

  const response = assertion.response as AuthenticatorAssertionResponse;

  return {
    mode: "webauthn",
    credentialId: credentialIdB64,
    clientDataJson: bufferToBase64(new Uint8Array(response.clientDataJSON)),
    authenticatorData: bufferToBase64(new Uint8Array(response.authenticatorData)),
    signature: bufferToBase64(new Uint8Array(response.signature)),
    pngDataUrl: renderPasskeyStamp(args.userEmail ?? "passkey"),
    widthPx: 320,
    heightPx: 96,
  };
}

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

/**
 * Render a simple "Signed with passkey — {identity}" stamp as a PNG data URL.
 * Used as the visual signature when the user picks WebAuthn.
 */
function renderPasskeyStamp(identity: string): string {
  const w = 320;
  const h = 96;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d")!;

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = "#7c6aff";
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, w - 2, h - 2);

  ctx.fillStyle = "#1a1a26";
  ctx.font = "600 16px -apple-system, Segoe UI, sans-serif";
  ctx.fillText("✓ Signed with passkey", 18, 30);

  ctx.fillStyle = "#55556a";
  ctx.font = "12px -apple-system, Segoe UI, sans-serif";
  ctx.fillText(identity, 18, 52);

  const ts = new Date().toISOString().replace("T", " ").slice(0, 16) + " UTC";
  ctx.fillText(ts, 18, 70);

  ctx.fillStyle = "#7c6aff";
  ctx.font = "10px SF Mono, Cascadia Code, monospace";
  ctx.fillText("etch.locker/assent", 18, 86);

  return canvas.toDataURL("image/png");
}
