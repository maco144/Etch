# Etch Assent — Product Spec

**Status:** V1 shipped, V2 shipped through send-to-sign; V3 not started
**Owner:** Alex
**Created:** 2026-04-21
**Updated:** 2026-09-08
**Location:** `~/etch/` (this repo)

> **Reading this document.** Sections marked *as built* describe shipped
> behavior and are authoritative — the code is the reference, this is the
> explanation. Sections marked *planned* are forward-looking and may not match
> what exists. V3 is entirely planned.

## Overview

Etch Assent is a client-side PDF signing web application powered by Etch's existing System of Records API (`/v1/records`). It captures the legal act of assent (agreement) with cryptographic provenance, without ever uploading the document to a vendor's servers.

It is a **feature of Etch**, not a standalone product, but it is also served at a consumer-friendly domain (`assent.to`) to capture discovery traffic that would bounce off Etch's API-focused positioning.

### What is shipped (as built)

| Capability | State | Where |
|---|---|---|
| Self-sign (drop → place → sign → download) | **Shipped** | `assent-app/src/routes/Sign.tsx` |
| Drawn signatures | **Shipped** | `lib/signatures.ts` |
| Passkey / WebAuthn signatures | **Shipped** | `lib/signatures.ts` |
| Optional text fields (printed name, date) | **Shipped** | `components/TextFieldOverlay.tsx` |
| Anonymous provenance chain | **Shipped** | `etch/assent_api.py` |
| Public verify page | **Shipped** | `routes/Verify.tsx`, `components/VerifyChain.tsx` |
| Send-to-sign, E2EE ciphertext store | **Shipped** | `etch/assent_docs_api.py`, `routes/Send.tsx` |
| Capability token gating document replacement | **Shipped** | `assent_docs_api.py` `_check_token` |
| Multiple signature fields + review/finalize gate | **Shipped** | `routes/Sign.tsx` |
| Resizable, draggable signature and text fields | **Shipped** | `hooks/useDraggableResizable.ts` |
| Customizable / removable verification stamp | **Shipped** | `components/StampFieldOverlay.tsx` |
| Email notification to counterparty | **Not shipped** — sender copies the link by hand | Resend integration deferred |
| Cloudflare R2 storage | **Not shipped** — local disk, IO narrowed to `_read`/`_write` for the swap | `assent_docs_api.py` |
| Accounts, billing, dashboard, webhooks (V3) | **Not started** | — |

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
- **`assent.to`** — consumer-facing; "free simple PDF signer" framing; SEO / PH / word-of-mouth. The name is a complete phrase: `assent.to/sign`

Same static bundle served from both. Marketing copy differs per entry point; core product identical.

---

## User Flows

### V1 — Self-sign (single user, own PDF) — *as built*

1. User visits `etch.locker/assent` (or consumer domain), drops a PDF
2. PDF renders in browser via PDF.js
3. User clicks to place a signature field on a page
4. User clicks Sign → chooses **Draw** or **Passkey**
   - Draw: canvas capture → PNG
   - Passkey: WebAuthn signs the document hash with platform authenticator (Touch ID, Windows Hello, YubiKey)
5. Steps 3–4 repeat for as many signature and text fields as the document needs.
   Every placed field can be dragged and resized before it is filled
6. User reviews the assembled document at the **review** gate, then finalizes
7. All signatures and text are flattened into the PDF via `pdf-lib`
8. Each step emits a record to Etch `/v1/assent/stamp` (parent-linked chain per document)
9. User downloads the signed PDF, carrying a QR verification stamp whose position,
   size, and presence they control
10. Anyone with the PDF visits `etch.locker/verify/{receipt_id}` and sees the full chain

**No account required.** Writes go to the public namespace `assent/public`.

A document finalizes on text fields alone — a signature field is not required.
Blank text fields do not count toward that gate, so a document of empty boxes
cannot finalize as an unchanged copy of itself.

### V2 — Send-to-sign (E2EE multi-party) — *as built*

1. Sender drops PDF and places fields for the counterparty
2. Browser generates a random AES-256-GCM key, encrypts the PDF, and uploads the
   ciphertext to `POST /v1/assent/document`, which returns a `document_id` **and a
   write token**
3. Sender stamps an `uploaded` event at index 0, binding themselves to the chain
   before the recipient ever touches the document
4. Sender copies and shares the link:
   `etch.locker/assent/sign/{doc_id}#key={b64key}&t={write_token}`. Both secrets
   ride in the fragment, which browsers never transmit
5. Recipient's browser fetches the ciphertext, decrypts in memory, signs,
   re-encrypts, and `PUT`s it back with `X-Assent-Write-Token`
6. Sender re-opens their own link and downloads through the same E2EE flow

**Still no account required** through V2.

*Planned, not shipped:* email notification. The sender currently copies the link
and delivers it themselves; a Resend integration would replace that step.

Both secrets living in one fragment is deliberate. The alternative — a
server-issued session — would give Etch something to leak. Anyone holding the
link can already decrypt the document, so gating the write on a token carried
the same way costs the recipient nothing and stops a passerby who guesses a
`document_id` from overwriting a document they cannot read.

### V3 — Accounts + billing — *planned, not started*

- Magic-link auth (reuse Etch API key infra; email-bound key issuance)
- Private Etch namespace per account
- Dashboard (sent / awaiting / completed)
- Stripe billing (Free + Pro $12/mo)
- Webhooks on `document.signed`

---

## Record Schema — *as built*

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
- `event_type` ∈ {`uploaded`, `created`, `field_added`, `signed`, `countersigned`, `finalized`}
  - `uploaded` is the send-to-sign opener: the sender stamps it at `event_index` 0
    so the chain records who put the document into circulation, not merely who
    signed it
- `document_hash` — SHA-256 of the PDF bytes *after* this event; for `created`, this is the original upload hash
- `parent_hash` — `document_hash` of the previous event; `null` only for `created`
- `signer` — present only for `signed` / `countersigned` events
- `location` — present only for `field_added` / `signed` events
- `timestamp` — client-provided; Etch server records its own `server_timestamp` independently (trust the later one)

### Chain invariants

Enforced server-side in `stamp_event`, not merely asserted:

- Events on a document are strictly ordered by `event_index`, and a submitted
  `event_index` must equal the count of events already recorded for that
  `document_id`. This rejects gaps, replays, and `document_id` squatting in a
  single indexed lookup
- `event_index == 0` must have no `parent_hash`; every later event must have one
- `parent_hash[N] == document_hash[N-1]` for all N > 0
- `document_hash` and `parent_hash` must be 64-character SHA-256 hex
- At most 32 events per document (`MAX_EVENTS_PER_DOCUMENT`) — a blast-radius
  ceiling on an anonymous endpoint
- Event payloads cap at 32 KB (`MAX_PAYLOAD_BYTES`)
- Client IPs are stored only as a salted hash (`ETCH_ASSENT_IP_SALT`)
- Any break in the chain means tampering or a client bug; the verify page flags this

---

## API Integration — *as built*

Assent got its own routers rather than an anonymous hole in `/v1/records`. Open
Decision 2 below was resolved in favour of a separate surface: the authenticated
SoR API stays clean, and every anonymous concern (rate limiting, namespace
pinning, event-index enforcement) lives in one place.

### Event chain — `etch/assent_api.py`

| Call | Purpose |
|---|---|
| `POST /v1/assent/stamp` | Emit one event; returns an `assent.receipt` |
| `GET /v1/assent/chain/{document_id}` | Full chain for a document, with `chain_intact` |
| `GET /v1/assent/records/{record_id}` | A single receipt |
| `GET /v1/assent/records/{record_id}/proof` | Offline-verifiable inclusion proof |
| `GET /v1/assent/verify?hash={sha256}` | Find events by document hash (recipient side) |

### Encrypted document store — `etch/assent_docs_api.py`

| Call | Purpose |
|---|---|
| `POST /v1/assent/document` | Upload ciphertext → `{ document_id, size, write_token }` |
| `GET /v1/assent/document/{doc_id}` | Download ciphertext (`application/octet-stream`) |
| `PUT /v1/assent/document/{doc_id}` | Replace with the signed version — requires `X-Assent-Write-Token` |
| `HEAD /v1/assent/document/{doc_id}` | Cheap existence check |

Storage is local disk at `ETCH_ASSENT_DOC_DIR` (default
`/var/etch/assent-documents`). All IO is narrowed to `_read` / `_write`, so
moving to Cloudflare R2 replaces two helpers and changes no HTTP behavior.

`PUT` authorizes *before* reading the body, so a rejected caller never gets to
push 15 MB at the server. It also 404s on a document that does not already exist,
which stops `PUT` being used as a disguised upload that skips the `POST` path.

The write token is returned exactly once, at upload. The server keeps only
`sha256(token)` in a sidecar file next to the ciphertext — a leak of Etch's disk
yields neither the document nor the right to replace it.

### Auth

- **V1 + V2:** Anonymous, namespace pinned to `assent/public`.
  - Events: 20 per IP per hour
  - Uploads: 10 per IP per hour, 15 MB per document (10 MB plaintext plus AES-GCM
    overhead plus room for a signed re-upload that adds a signature image)
  - Document replacement additionally requires the capability token issued at
    upload time
- **V3 (planned):** Standard `Bearer etch_{mode}_sk_{token}` flow, tied to a user
  account, writing to `assent/{account_id}`

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

`assent.to` points to the same server, serves the `site/assent/` bundle at root, and proxies `/v1/*` to the Etch API. Both site blocks live in `deploy/Caddyfile.assent`.

### Codebase layout

```
~/etch/
├── site/
│   ├── index.html            # existing landing (untouched)
│   └── assent/               # NEW — built output
│       ├── index.html
│       └── assets/*
├── assent-app/               # frontend source
│   ├── package.json
│   ├── vite.config.ts        # builds to ../site/assent/
│   ├── tailwind.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── routes/
│       │   ├── Home.tsx              # drop-a-PDF landing
│       │   ├── Sign.tsx              # signing workflow (place → review → finalize)
│       │   ├── Send.tsx              # send-to-sign: encrypt, upload, build share link
│       │   └── Verify.tsx            # /verify/:id page
│       ├── lib/
│       │   ├── etch.ts               # /v1/assent/* browser client
│       │   ├── pdf.ts                # pdf-lib + PDF.js glue
│       │   ├── signatures.ts         # draw vs webauthn handlers
│       │   ├── hash.ts               # SHA-256 helpers (WebCrypto)
│       │   ├── crypto.ts             # AES-256-GCM E2EE helpers
│       │   ├── routing.ts            # URL fragment parsing (#key=…&t=…)
│       │   └── handoff.ts            # in-memory PDF handoff, Home → Sign
│       ├── hooks/
│       │   └── useDraggableResizable.ts   # shared drag + resize behavior
│       └── components/
│           ├── PdfViewer.tsx
│           ├── SignatureField.tsx
│           ├── SignaturePad.tsx
│           ├── TextFieldOverlay.tsx
│           ├── StampFieldOverlay.tsx
│           └── VerifyChain.tsx
├── etch/
│   ├── assent_api.py         # event chain router (anonymous, rate-limited)
│   └── assent_docs_api.py    # E2EE ciphertext store (send-to-sign)
└── docs/
    └── ETCH_ASSENT_SPEC.md   # this document
```

`handoff.ts` stages the dropped PDF in a module-scoped `Map` rather than
`sessionStorage`: round-tripping a 10 MB `Uint8Array` through `JSON.stringify`
costs roughly 60 MB, which is enough to kill the tab on mobile Safari.

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

## V1 Ship Plan — *shipped 2026-04-21/22*

### Week 1 — Core signing flow

- [x] Scaffold `assent-app/` (Vite + React + TS + Tailwind)
- [x] Vite config builds to `../site/assent/`
- [x] `Home.tsx`: drop-a-PDF landing; copy matches Etch brand voice
- [x] `PdfViewer.tsx`: PDF.js render, page navigation
- [x] `SignatureField.tsx`: click-to-place signature box (single field for V1)
- [x] `SignaturePad.tsx`: HTML5 canvas, mouse + touch, outputs PNG data URL
- [x] `signatures.ts` — WebAuthn flow:
  - Compute SHA-256 of original PDF bytes (`hash.ts`)
  - `navigator.credentials.create({ publicKey: { challenge: hashBytes, ... } })` first time
  - `navigator.credentials.get()` for subsequent signs
  - Store credential ID, attestation in record payload
- [x] `pdf.ts`:
  - Flatten signature PNG via `pdf-lib`
  - Embed receipt ID in PDF metadata (`setSubject` or custom keyword)
  - Generate QR code → last page watermark
- [x] `etch.ts`: thin `POST /v1/records` wrapper with chain linkage helpers
- [x] `Verify.tsx` route (`/verify/:id`): fetch chain via `GET /v1/records?document_id=...`, render Stripe-style receipt (see UX notes below)
- [x] Backend: anonymous-write endpoint for `assent/public` with IP rate limiting — shipped as a dedicated router, `POST /v1/assent/stamp`
- [x] Caddy config for `/assent/*` and `/verify/*`

### Week 2 — Polish, error paths, launch

- [x] Error handling: corrupted PDFs, >10MB, password-protected (show message, don't crash)
- [x] Mobile touch signing (iOS Safari, Android Chrome)
- [x] Verify page polish — this is the marketing surface; get it Stripe-receipt-clean
- [x] Landing page copy, OG image, favicon
- [x] Consumer domain DNS + Caddy config — `assent.to`, wired in `deploy/Caddyfile.assent`
- [ ] Soft launch (HN Show, relevant subreddits, Twitter, a few practitioners) — **not done**

### Out of V1 scope

Accounts, send-to-sign, payments, multi-party, templates, text fields, OCR, non-PDF formats, password-protected PDFs, mobile app.

Since shipped: **text fields** (2026-04-22), **send-to-sign** (2026-04-22), and
**multi-party in the mechanical sense** — several independently placed and signed
signature fields per document (2026-07-31). Routing a document between named
parties, with permissions per field, remains out of scope; the `label` on a
signature field is display-only and deliberately carries no routing logic.

---

## V2 Ship Plan — *shipped 2026-04-22/24, except email*

- [ ] Cloudflare R2 bucket provisioned (30-day TTL) — **deferred**; local disk
      behind `_read`/`_write`, so the swap is two functions
- [x] `etch/assent_docs_api.py` — ciphertext store (POST/GET/PUT/HEAD)
- [ ] Resend integration for notification emails — **deferred**; sender copies
      the link by hand
- [x] `crypto.ts` — AES-256-GCM encrypt/decrypt, random key generation via WebCrypto
- [x] Send flow UI (field placement for counterparty, share-link + QR generation)
- [x] Sign-link UX (extract fragment key, fetch ciphertext, decrypt, sign, re-encrypt, upload)
- [x] Sender bound to the chain via an `uploaded` event at index 0
- [x] Capability token gating `PUT`, delivered in the link fragment
- [ ] Sender notification on `document.signed` event — **deferred** with email
- [x] Still no accounts

### Shipped after V2 — signing ergonomics (2026-07-31)

- [x] Multiple signature fields per document; placement appends instead of replacing
- [x] Resize and drag-move on signature fields, text fields, and the stamp
      (`hooks/useDraggableResizable.ts`)
- [x] Review/finalize gate between placing and download
- [x] Verification stamp is draggable, resizable, and can be turned off; the rect
      survives a toggle, so switching it off is never destructive
- [x] Stamp shrunk from a full-width banner to a corner badge
- [x] Finalize on text fields alone; blank fields don't count toward the gate

---

## V3 Ship Plan — *not started*

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

1. ~~**Consumer domain.**~~ **Resolved: `assent.to`.** It reads as a complete
   English phrase — `assent.to/sign` — so the SPA drops the `/assent/` prefix there
   and serves from root. Both prefixes ship in one bundle:
   `deploy/Caddyfile.assent` carries the site block, `lib/routing.ts` resolves which
   prefix is active. (Registration and DNS state are not verifiable from this repo;
   the serving config assumes the domain resolves to the same host.)

2. ~~**Anonymous V1 writes.** Add a new endpoint or extend `records_api.py`?~~
   **Resolved:** dedicated router. `POST /v1/assent/stamp` owns its own rate
   limiting, namespace pin, and event-index enforcement; `/v1/records` stayed
   authenticated and clean.

3. **Receipt sidecar format.** Download receipt as separate JSON, or embed in PDF metadata only?
   - Recommend: **both**. PDF metadata for inline verification; separate `.receipt.json` download for offline archive.

4. **Password-protected PDFs.** Support in V1 or defer?
   - Deferred past V2 as well. Still open; still requires a password prompt UX and
     complicates the flatten flow.

5. **Signature types beyond WebAuthn + drawn.** Typed-cursive-font signatures? Initials vs full signature?
   - Recommend: typed + initials in V3. V1 keeps scope tight.

6. ~~**Verification for non-Etch-signed PDFs uploaded to `/verify`.**~~
   **Resolved:** explicit "not verified" message, per the UX section above.

7. **Email delivery.** Resend integration for send-to-sign notifications is
   specified but unbuilt; senders currently hand-deliver the link. This is the
   largest remaining gap in V2 and the most likely next unit of work.

8. **R2 migration.** Local disk is fine at current volume. The trigger to move is
   either multi-host deployment or documents outliving a single server's disk;
   the code is already shaped for it.

---

## References

- [`CLAUDE.md`](../CLAUDE.md) — Etch repo conventions
- [`PROJECT_INDEX.md`](../PROJECT_INDEX.md) — existing API surface and module map
- [`records_api.py`](../etch/records_api.py) — SoR API this feature builds on
- [`auth.py`](../etch/auth.py) — API key auth to reuse for V3 accounts
- [`site/index.html`](../site/index.html) — existing landing (brand reference)
- [`assent_api.py`](../etch/assent_api.py) — event chain router
- [`assent_docs_api.py`](../etch/assent_docs_api.py) — E2EE ciphertext store
- [`specs/2026-07-31-assent-resize-multi-signature-design.md`](superpowers/specs/2026-07-31-assent-resize-multi-signature-design.md) — resizable fields + review/finalize design
- [`specs/2026-07-31-assent-customizable-qr-stamp-design.md`](superpowers/specs/2026-07-31-assent-customizable-qr-stamp-design.md) — customizable stamp design

---

## Next actions

1. Resend integration so a sender can send rather than copy a link (Open Decision 7).
   This is the largest functional gap in V2 — send-to-sign works end to end, but the
   sender has to deliver the link themselves.
2. Soft launch. Everything V1 promised has shipped, `assent.to` is configured, and the
   product has been sitting finished since April.

These are independent of each other and of any further signing work.
