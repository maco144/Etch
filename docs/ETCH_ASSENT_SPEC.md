# Etch Assent — Product Spec (V1)

**Status:** Draft
**Owner:** Alex
**Created:** 2026-04-21
**Location:** `~/etch/` (this repo)

## Overview

Etch Assent is a client-side PDF signing web application powered by Etch's existing System of Records API (`/v1/records`). It captures the legal act of assent (agreement) with cryptographic provenance, without ever uploading the document to a vendor's servers.

It is a **feature of Etch**, not a standalone product, but it is also served at a consumer-friendly domain (e.g. `simplepdfsigner.com`) to capture discovery traffic that would bounce off Etch's API-focused positioning.

### Positioning

> **Etch Assent — permanent proof of agreement. No vendor to trust.**

One-line pitch: *DocuSign, but the PDF never leaves your browser and the audit trail is cryptographically verifiable by anyone, forever.*

### Wedge vs. incumbents

| Capability | DocuSign | DocuSeal | **Etch Assent** |
|---|---|---|---|
| Document never uploaded to vendor | No | No | **Yes (E2EE in V2+)** |
| Audit trail independently verifiable offline | No | No | **Yes (Etch Merkle chain)** |
| Passkey / WebAuthn signing | No | No | **Yes** |
| Proof survives vendor shutdown | No | No | **Yes (offline-verifiable)** |
| Qualified eIDAS (EU) | Yes | Yes | No — **do not compete here** |
| Free for individuals | Limited (3/mo) | Yes | **Yes (unlimited self-sign)** |

We do not match full DocuSign/DocuSeal feature breadth (templates, CRM integrations, bulk send). We own a trust-architecture axis neither structurally competes on.

### Distribution

Dual-domain, single app bundle:

- **`etch.locker/assent`** — primary home; trust-architecture framing; B2B / developer / API audience
- **`{consumer-domain}.com`** (TBD — candidates below) — consumer-facing; "free simple PDF signer" framing; SEO / PH / word-of-mouth

Same static bundle served from both. Marketing copy differs per entry point; core product identical.

---

## User Flows

### V1 — Self-sign (single user, own PDF)

1. User visits `etch.locker/assent` (or consumer domain), drops a PDF
2. PDF renders in browser via PDF.js
3. User clicks to place a signature field on a page
4. User clicks Sign → chooses **Draw** or **Passkey**
   - Draw: canvas capture → PNG
   - Passkey: WebAuthn signs the document hash with platform authenticator (Touch ID, Windows Hello, YubiKey)
5. Signature is flattened into the PDF via `pdf-lib`
6. Each step emits a record to Etch `/v1/records` (parent-linked chain per document)
7. User downloads the signed PDF (with receipt ID + QR code embedded on last page)
8. Anyone with the PDF visits `etch.locker/verify/{receipt_id}` and sees the full chain

**No account required.** Writes go to the public namespace `assent/public`.

### V2 — Send-to-sign (E2EE multi-party)

1. Sender drops PDF, places fields for counterparty, types counterparty email
2. Browser generates random AES-256-GCM key, encrypts PDF, uploads ciphertext to R2
3. Sender copies/shares link: `etch.locker/assent/sign/{doc_id}#key={b64key}`
4. Recipient's browser fetches ciphertext, decrypts in-memory (fragment key never sent to server), signs, re-encrypts, uploads
5. Sender gets email notification, downloads via same E2EE flow

**Still no account required** through V2.

### V3 — Accounts + billing

- Magic-link auth (reuse Etch API key infra; email-bound key issuance)
- Private Etch namespace per account
- Dashboard (sent / awaiting / completed)
- Stripe billing (Free + Pro $12/mo)
- Webhooks on `document.signed`

---

## Record Schema

Every document's audit trail is a chain of Etch records linked by `parent_hash`. Record creation is **the only** interaction with Etch — there is no new data model.

**Namespace:** `assent/public` (V1), `assent/{account_id}` (V3)
**Kind:** `assent.event`

```json
{
  "kind": "assent.event",
  "schema_version": 1,
  "document_id": "doc_2kX9abc...",
  "event_type": "created",
  "document_hash": "sha256:...",
  "parent_hash": "sha256:...",
  "event_index": 0,
  "signer": {
    "method": "webauthn",
    "credential_id": "b64...",
    "attestation": "b64...",
    "email": "alice@example.com",
    "name": "Alice Smith"
  },
  "location": {
    "page": 2,
    "x": 140,
    "y": 680,
    "width": 200,
    "height": 60
  },
  "timestamp": "2026-04-21T14:32:17Z",
  "client_metadata": {
    "user_agent": "...",
    "platform": "macOS"
  }
}
```

### Field notes

- `document_id` — stable across all events on this document; client-generated UUID
- `event_type` ∈ {`created`, `field_added`, `signed`, `countersigned`, `finalized`}
- `document_hash` — SHA-256 of the PDF bytes *after* this event; for `created`, this is the original upload hash
- `parent_hash` — `document_hash` of the previous event; `null` only for `created`
- `signer` — present only for `signed` / `countersigned` events
- `location` — present only for `field_added` / `signed` events
- `timestamp` — client-provided; Etch server records its own `server_timestamp` independently (trust the later one)

### Chain invariants

- Events on a document are strictly ordered by `event_index`
- `parent_hash[N] == document_hash[N-1]` for all N > 0
- Any break in the chain means tampering or client bug; verify page flags this

---

## API Integration

Assent uses only Etch's existing `/v1/records` surface. **No new backend endpoints required for V1.**

| Call | Purpose |
|---|---|
| `POST /v1/records` | Emit each event (one call per event) |
| `GET /v1/records?document_id={id}` | Fetch full chain for a document (verify page) |
| `GET /v1/records/{id}/proof` | Offline-verifiable inclusion proof |

### Auth

- **V1:** Anonymous writes to `assent/public`. Requires a small addition to `records_api.py`: a public, rate-limited endpoint that accepts writes without an API key but restricted to the `assent/public` namespace only.
  - Rate limit: 20 events per IP per hour
  - PDF size limit (client-enforced; server doesn't see the PDF): 10 MB
- **V3:** Standard `Bearer etch_{mode}_sk_{token}` flow, tied to user account, writes to `assent/{account_id}`

### V2 additional endpoints (send-to-sign)

New FastAPI router at `etch/assent_api.py`:

- `POST /v1/assent/upload` → presigned R2 URL + `doc_id`
- `POST /v1/assent/notify` → sends email via Resend
- `GET /v1/assent/sign/{id}` → returns R2 ciphertext URL + metadata

---

## Architecture

### Deployment

```
                         Caddy (rising server, etch.locker)
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
    /  → site/index.html       /assent/* → site/assent/        /v1/* → etch API :8101
    (existing)                 (NEW — built static bundle)     (existing)
```

Consumer domain (e.g. `simplepdfsigner.com`) points to the same server, serves the `site/assent/` bundle at root, and proxies `/v1/*` to the Etch API.

### Codebase layout

```
~/etch/
├── site/
│   ├── index.html            # existing landing (untouched)
│   └── assent/               # NEW — built output
│       ├── index.html
│       └── assets/*
├── assent-app/               # NEW — frontend source
│   ├── package.json
│   ├── vite.config.ts        # builds to ../site/assent/
│   ├── tailwind.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── routes/
│       │   ├── Home.tsx              # drop-a-PDF landing
│       │   ├── Sign.tsx              # signing workflow
│       │   └── Verify.tsx            # /verify/:id page
│       ├── lib/
│       │   ├── etch.ts               # POST /v1/records wrapper
│       │   ├── pdf.ts                # pdf-lib + PDF.js glue
│       │   ├── signatures.ts         # draw vs webauthn handlers
│       │   ├── hash.ts               # SHA-256 helpers (WebCrypto)
│       │   └── crypto.ts             # V2 — AES-GCM E2EE helpers
│       └── components/
│           ├── PdfViewer.tsx
│           ├── SignatureField.tsx
│           ├── SignaturePad.tsx
│           └── VerifyChain.tsx
├── etch/
│   └── assent_api.py         # V2 — FastAPI router for E2EE send-to-sign
└── docs/
    └── ETCH_ASSENT_SPEC.md   # this document
```

### Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Framework | React 18 + Vite + TypeScript | Fully static output, no SSR |
| Routing | react-router-dom | SPA, Caddy handles fallback |
| PDF render | `pdfjs-dist` | Canvas-based |
| PDF edit | `pdf-lib` | Flatten signature images |
| QR codes | `qrcode` | Last-page embed |
| Crypto | WebCrypto + WebAuthn | Native browser APIs |
| Styling | Tailwind CSS | Dark theme matching `site/index.html` |
| Build | Vite → static | Output goes to `../site/assent/` |

### Caddy routing (add to existing config)

```
etch.locker {
    # existing: static root at site/index.html, /v1/* proxied to :8101

    handle /assent/* {
        root * /opt/etch/site
        try_files {path} /assent/index.html
        file_server
    }

    handle /verify/* {
        root * /opt/etch/site
        try_files {path} /assent/index.html
        file_server
    }
}
```

---

## V1 Ship Plan (Week 1-2)

### Week 1 — Core signing flow

- [ ] Scaffold `assent-app/` (Vite + React + TS + Tailwind)
- [ ] Vite config builds to `../site/assent/`
- [ ] `Home.tsx`: drop-a-PDF landing; copy matches Etch brand voice
- [ ] `PdfViewer.tsx`: PDF.js render, page navigation
- [ ] `SignatureField.tsx`: click-to-place signature box (single field for V1)
- [ ] `SignaturePad.tsx`: HTML5 canvas, mouse + touch, outputs PNG data URL
- [ ] `signatures.ts` — WebAuthn flow:
  - Compute SHA-256 of original PDF bytes (`hash.ts`)
  - `navigator.credentials.create({ publicKey: { challenge: hashBytes, ... } })` first time
  - `navigator.credentials.get()` for subsequent signs
  - Store credential ID, attestation in record payload
- [ ] `pdf.ts`:
  - Flatten signature PNG via `pdf-lib`
  - Embed receipt ID in PDF metadata (`setSubject` or custom keyword)
  - Generate QR code → last page watermark
- [ ] `etch.ts`: thin `POST /v1/records` wrapper with chain linkage helpers
- [ ] `Verify.tsx` route (`/verify/:id`): fetch chain via `GET /v1/records?document_id=...`, render Stripe-style receipt (see UX notes below)
- [ ] Backend: new anonymous-write endpoint for `assent/public` namespace with IP rate limiting (modify `etch/records_api.py` or add a public shim)
- [ ] Caddy config for `/assent/*` and `/verify/*`

### Week 2 — Polish, error paths, launch

- [ ] Error handling: corrupted PDFs, >10MB, password-protected (show message, don't crash)
- [ ] Mobile touch signing (iOS Safari, Android Chrome)
- [ ] Verify page polish — this is the marketing surface; get it Stripe-receipt-clean
- [ ] Landing page copy, OG image, favicon
- [ ] Consumer domain DNS + Caddy config
- [ ] Soft launch (HN Show, relevant subreddits, Twitter, a few practitioners)

### Out of V1 scope

Accounts, send-to-sign, payments, multi-party, templates, text fields, OCR, non-PDF formats, password-protected PDFs, mobile app.

---

## V2 Ship Plan (Week 3-4) — Send-to-sign, E2EE

- [ ] Cloudflare R2 bucket provisioned (30-day TTL)
- [ ] `etch/assent_api.py` with three endpoints (upload presign, notify, fetch)
- [ ] Resend integration for notification emails
- [ ] `crypto.ts` — AES-256-GCM encrypt/decrypt, random key generation via WebCrypto
- [ ] Send flow UI (counterparty email input, fields placement for counterparty)
- [ ] Sign-link UX (extract fragment key, fetch ciphertext, decrypt, sign, re-encrypt, upload)
- [ ] Sender notification on `document.signed` event
- [ ] Still no accounts

---

## V3 Ship Plan (Week 5-6) — Accounts + billing

- [ ] Magic-link auth: issue `etch_live_sk_*` keys tied to verified email
- [ ] Dashboard route: list documents in sent / awaiting / completed
- [ ] Namespace scoping: writes go to `assent/{account_id}`
- [ ] Stripe: single product, two prices
  - Free: 5 sends/mo, public namespace, drawn signatures only
  - Pro: $12/mo, unlimited, private namespace, passkey signing, webhooks
- [ ] Webhook delivery with retry on `document.signed`
- [ ] Usage enforcement (in `records_api.py` rate limiter)

---

## UX — Verify Page

This is the single most important screen. Every signed PDF links here. It must feel like a Stripe receipt, not a blockchain explorer.

### Content

```
✓ Document verified

  Hash matches the signed PDF you provided.

Signed by alice@example.com
  via WebAuthn (Touch ID on MacBook Pro)
  on 2026-04-21 at 14:32:17 UTC

Provenance chain (Etch)
  Namespace:  assent/public
  Document:   doc_2kX9abc...
  Events:     4 (created → field_added → signed → finalized)

  [Show full chain ▾]    [Download receipt JSON]    [Verify with Etch CLI]
```

### Non-goals

- Do not show raw Merkle roots, leaf indexes, or hashes in the default view
- Do not use the word "blockchain"
- Do not require the user to understand cryptography to trust the result

### Anti-tamper flow

If the user uploads a PDF to verify and its hash doesn't match any record's `document_hash`:

```
✗ Not verified

  This document does not match any signature in the Etch chain.
  Either it has been modified after signing, or it was never signed
  with Etch Assent.
```

---

## Legal Positioning

**Claim:**
- ESIGN/UETA (US) compliant — all signature modes
- eIDAS "Advanced" — passkey mode only
- Tamper-evident audit trail via Etch Merkle chain
- Independently verifiable offline (any party can audit via Etch SDK without API access)

**Do not claim:**
- eIDAS "Qualified" — requires certified TSP, DocuSeal/Adobe's regulatory moat; orthogonal to our pitch
- "Legally binding in all jurisdictions" — jurisdictional claims require counsel review
- HIPAA compliance (V1) — requires BAA + enterprise features; defer to V3+

---

## Open Decisions

1. **Consumer domain.** User plans to buy a generic "simple PDF signer" domain. Shortlist to finalize:
   - `simplepdfsigner.com` (user's initial thought)
   - `signpdf.app`, `quicksign.app`, `inkedit.app`
   - `agreedto.com`, `signed.at`
   - Recommend: short (≤10 chars pre-TLD), `.app` or `.com`, no hyphens

2. **Anonymous V1 writes.** Add a new endpoint or extend `records_api.py`?
   - Recommend: new public endpoint `/v1/assent/stamp` with its own middleware (rate limit, namespace pin). Keep the existing authed `/v1/records` clean.

3. **Receipt sidecar format.** Download receipt as separate JSON, or embed in PDF metadata only?
   - Recommend: **both**. PDF metadata for inline verification; separate `.receipt.json` download for offline archive.

4. **Password-protected PDFs.** Support in V1 or defer?
   - Recommend: defer to V2. Requires a password prompt UX and complicates flatten flow.

5. **Signature types beyond WebAuthn + drawn.** Typed-cursive-font signatures? Initials vs full signature?
   - Recommend: typed + initials in V3. V1 keeps scope tight.

6. **Verification for non-Etch-signed PDFs uploaded to `/verify`.** Show clear "not found in chain" message vs. silently fail.
   - Recommend: explicit message (see UX section above).

---

## References

- [`CLAUDE.md`](../CLAUDE.md) — Etch repo conventions
- [`PROJECT_INDEX.md`](../PROJECT_INDEX.md) — existing API surface and module map
- [`records_api.py`](../etch/records_api.py) — SoR API this feature builds on
- [`auth.py`](../etch/auth.py) — API key auth to reuse for V3 accounts
- [`site/index.html`](../site/index.html) — existing landing (brand reference)

---

## Next actions

1. Confirm consumer domain → update spec and Caddy config
2. Approve anonymous-write endpoint design (new route vs. extension)
3. Hand off to implementation — this spec is designed to be self-sufficient for a fresh Claude instance or a developer to build from
