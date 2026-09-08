# Project Index: Etch

Generated: 2026-09-08

## Project Structure

```
etch/
├── etch/
│   ├── __init__.py          # Exports: AuditChain, ChainEntry, InclusionProof, verify_inclusion_proof, EtchClient
│   ├── chain.py             # Core MMR audit chain (hash chain, proofs, verification)
│   ├── chain_manager.py     # Namespace-isolated chain manager (per-tenant chains)
│   ├── models.py            # SQLAlchemy ORM: ProofRecord, Namespace, ApiKey, RecordEntry
│   ├── db.py                # Async DB session (PostgreSQL or SQLite fallback)
│   ├── auth.py              # API key auth (Bearer etch_{mode}_sk_{token}), namespace bootstrap
│   ├── api.py               # FastAPI router: /v1/proof/* (legacy, simple registration)
│   ├── records_api.py       # FastAPI router: /v1/records/* (SoR API, namespace-isolated)
│   ├── c2pa.py              # FastAPI router: /v1/c2pa/* (C2PA manifest bridge)
│   ├── assent_api.py        # FastAPI router: /v1/assent/* (anonymous PDF-signer events)
│   ├── assent_docs_api.py   # FastAPI router: /v1/assent/document/* (E2EE ciphertext blobs for send-to-sign)
│   ├── dooh_api.py          # FastAPI router: /v1/dooh/* (playback receipts, API key auth)
│   ├── glyph.py             # Shortcodes (Crockford base32 + checksum) + bar/QR sigil rendering
│   ├── glyph_api.py         # FastAPI router: /v1/proof/{id}/glyph + public resolver at /g/*
│   ├── watermark_api.py     # FastAPI router: audio embed/extract endpoints
│   ├── dooh/                # DOOH receipt SDK (Apache-2.0)
│   │   ├── canonical.py     # RFC 8785 (JCS) wrapper — deterministic signing bytes
│   │   ├── keys.py          # KeyPair — ed25519 generate/save/load/sign
│   │   ├── receipt.py       # Receipt, SignedBundle, EtchProof, sign/countersign
│   │   ├── manifest.py      # IdentityManifest — advertiser-published key_id → pubkey
│   │   ├── submitter.py     # EtchSubmitter — sync httpx client (stored + hash-only modes)
│   │   ├── verifier.py      # verify_bundle() — six-step offline verification
│   │   └── README.md        # SDK quickstart + architecture
│   ├── watermark/           # etchmark — inaudible audio watermark
│   │   ├── payload.py       # Bit packing, CRC-12, sync pattern
│   │   ├── sync.py          # Chunk alignment / sync recovery
│   │   ├── spread.py        # Spread-spectrum FFT layer
│   │   ├── io.py            # Audio decode (WAV/FLAC native, else ffmpeg) + WAV write
│   │   └── pipeline.py      # High-level embed() / extract()
│   ├── sdk.py               # Async Python SDK (EtchClient)
│   └── server.py            # FastAPI app entrypoint, lifespan, logging, /health
├── assent-app/              # React+Vite frontend for Etch Assent (builds → site/assent/)
│   ├── src/routes/          # Home, Sign, Send, Verify
│   ├── src/components/      # PdfViewer, SignatureField, SignaturePad, TextFieldOverlay, StampFieldOverlay, VerifyChain
│   ├── src/hooks/           # useDraggableResizable (shared drag + resize for all field overlays)
│   └── src/lib/             # crypto (AES-GCM WebCrypto), hash, etch, pdf (pdf-lib + PDF.js), signatures, handoff, routing
├── tests/
│   ├── test_chain.py              # Unit tests: AuditChain, InclusionProof, verify
│   ├── test_api.py                # Legacy API tests (httpx + ASGI, mocked DB)
│   ├── test_sdk.py                # SDK client tests
│   ├── test_batch_api.py          # Batch registration tests
│   ├── test_c2pa.py               # C2PA compatibility tests
│   ├── test_records_api.py        # SoR API tests
│   ├── test_assent_api.py         # Etch Assent public event/chain API tests
│   ├── test_assent_docs_api.py    # Encrypted document storage API tests (V2)
│   ├── test_dooh.py               # DOOH SDK: unit + integration + e2e
│   ├── test_glyph.py              # Shortcode encode/decode, checksum, sigil render
│   ├── test_watermark.py          # Payload framing, sync, spread-spectrum layer
│   ├── test_watermark_api.py      # Embed/extract endpoint tests
│   └── test_watermark_robustness.py  # Survival across lossy re-encode
├── docs/
│   ├── ETCH_ASSENT_SPEC.md        # Assent product + protocol spec (as-built + planned)
│   ├── dooh-spec.md               # DOOH playback receipt spec (CC-BY 4.0)
│   ├── RELEASING.md               # Release procedure (PyPI via OIDC)
│   ├── superpowers/specs/         # Design specs for shipped Assent features
│   ├── superpowers/plans/         # Implementation plans for the same
│   └── _private/                  # Gitignored — outreach, pipeline, licensing
├── examples/
│   ├── dooh_demo.py               # End-to-end: anchor N plays, query back, verify
│   └── dooh_reference_player.py   # Drives a real Etch endpoint, writes bundles
├── deploy/
│   ├── Caddyfile.assent    # Caddy config for assent.etch.locker (serves SPA + proxies /v1/*)
│   └── nginx.conf          # Legacy nginx config (unused in prod — Caddy serves etch.locker)
├── site/
│   ├── index.html          # Landing page (dark theme, self-contained)
│   └── assent/             # Built React SPA (output of `vite build` in assent-app/)
├── .github/workflows/
│   ├── ci.yml              # Test matrix: Python 3.11/3.12/3.13, ruff lint, assent-frontend (tsc + vite build)
│   └── release.yml         # PyPI publish on git tag v*
├── pyproject.toml          # Hatch build, deps, pytest/ruff config
├── Dockerfile              # Python 3.12-slim, non-root, port 8100
├── docker-compose.yml      # etch (8101) + postgres + nginx
├── README.md               # Quick start, full API reference, configuration
├── CHANGELOG.md            # Keep a Changelog format; v0.2.0 tagged, 32 commits unreleased
└── LICENSE.md              # Rising Sun License v1.0
```

## Entry Points

- **Server**: `etch/server.py` — `uvicorn etch.server:app --reload` (port 8100)
- **Library**: `from etch import AuditChain, verify_inclusion_proof, EtchClient`
- **Assent frontend**: `cd assent-app && npm run dev` (Vite dev server) or `npm run build` → `site/assent/`
- **Tests**: `pytest` (asyncio_mode=auto)
- **Docker**: `docker-compose up` (etch:8101, postgres, nginx:80)

## API Surface

### /v1/proof/* (Legacy — simple registration, no auth)
- `POST /v1/proof` — Register content/hash → ProofReceipt
- `POST /v1/proof/batch` — Batch register (up to 1000 items)
- `GET /v1/proof/{proof_id}` — Lookup by leaf_index
- `GET /v1/proof/hash/{content_hash}` — Lookup by SHA-256
- `GET /v1/proof/recent` — Paginated listing
- `GET /v1/proof/stats` — Chain statistics
- `POST /v1/proof/{proof_id}/verify` — Verify content integrity

### /v1/records/* (SoR API — namespace-isolated, API key auth)
- `POST /v1/records` — Create record receipt (`if_changed: true` + `record.id` → append only when the content hash changed; unchanged content returns the existing receipt with `deduplicated: true`)
- `GET /v1/records` — List/filter with cursor pagination
- `GET /v1/records/{record_id}` — Retrieve receipt
- `GET /v1/records/{record_id}/proof` — Self-contained inclusion proof
- `POST /v1/records/verify` — Verify record against chain
- `GET /v1/records/stats` — Append vs. dedup counters for this namespace
- `GET /v1/chain/root` — Current chain state

### /v1/assent/* (Etch Assent — anonymous, rate-limited)
Event/chain endpoints (namespace pinned to `assent/public`, 20 events/hr per IP):
- `POST /v1/assent/stamp` — Commit an `assent.event` (uploaded/created/field_added/signed/countersigned/finalized); server enforces `event_index == existing_count`, 32 events/doc, 32 KB payload
- `GET /v1/assent/chain/{document_id}` — Fetch the event chain with integrity check
- `GET /v1/assent/records/{record_id}` — Fetch a single receipt
- `GET /v1/assent/records/{record_id}/proof` — Self-contained inclusion proof (public)
- `GET /v1/assent/verify?hash={sha256}` — Find events by document hash (recipient-side)

E2EE document storage (send-to-sign, V2 slice 1 — 10 uploads/hr per IP, 15 MB cap):
- `POST /v1/assent/document` — Upload opaque ciphertext → `{ document_id, size, write_token }`
- `GET  /v1/assent/document/{doc_id}` — Download ciphertext (octet-stream)
- `PUT  /v1/assent/document/{doc_id}` — Replace (signed re-upload); requires `X-Assent-Write-Token`, only `sha256(token)` is stored
- `HEAD /v1/assent/document/{doc_id}` — Existence check

Plaintext PDFs are encrypted client-side with AES-256-GCM. The key lives in the
URL fragment (`…#key=<b64>`) which browsers never transmit, so Etch cannot
decrypt anything it stores. Disk path configurable via `ETCH_ASSENT_DOC_DIR`
(default `/var/etch/assent-documents`); designed so a future Cloudflare R2
swap is just `_read` / `_write`.

### /v1/c2pa/* (C2PA bridge — no auth)
- `POST /v1/c2pa/manifest` — Register C2PA manifest on chain
- `GET /v1/c2pa/manifest/{claim_id}` — Retrieve manifest + Etch proof
- `POST /v1/c2pa/verify` — Verify manifest + chain
- `POST /v1/c2pa/bridge` — Bridge existing proof to C2PA format

### /v1/dooh/* (DOOH playback receipts — API key auth)
- `POST /v1/dooh/receipts` — Anchor a `SignedBundle`, store it, return bundle + proof
- `GET /v1/dooh/receipts` — Query by `campaign_id`, `screen_id`, `played_at_from`, `played_at_to`
- `POST /v1/dooh/verify` — Server-side run of the offline verifier

Two anchoring modes: `submitter.submit()` stores hash + bundle JSON;
`submit_hash_only()` stores the hash alone (advertiser hosts bundles). Same hash,
same chain — the choice is only about where the JSON lives.

### Glyph (shortcodes + public resolver — no auth)
- `POST /v1/proof/{proof_id}/glyph` — Render bar sigil + magic URL bundle
- `GET /g/{shortcode}` — Public resolver page (server-rendered HTML, no JS framework)
- `GET /g/{shortcode}.json` — Resolver result as JSON (for SDKs)
- `GET /g/{shortcode}.png` — Bar sigil PNG (hot-linkable)

Shortcode = 8 chars Crockford base32 over `[32-bit proof_id][8-bit checksum]`.
The resolver verifies the embedded checksum against the stored `content_hash` and
refuses to resolve on mismatch, so typos and fabrications fail closed.

### Watermark / etchmark (audio embed + extract — no auth)
- `POST /v1/proof/{proof_id}/embed-audio` — Multipart upload → watermarked 16-bit PCM WAV
- `POST /v1/proof/extract-audio` — Multipart upload → recovered shortcode + resolver URL

Embed returns lossless WAV deliberately: the artist's own lossy encode is the
expected next step, and double-encoding would waste headroom. Extract is
read-only and registers nothing.

### Other
- `GET /health` → `{"status":"ok","service":"etch","version":"0.2.0"}`

## Core Modules

### chain.py — MMR Audit Chain
- `AuditChain` — Thread-safe in-memory chain with persist hooks
- `ChainEntry` — Dataclass: leaf_index, leaf_hash, mmr_root, payload_hash, action_type, timestamps
- `InclusionProof` — Offline-verifiable proof (no server trust needed)
- `verify_inclusion_proof()` — Standalone verification
- `get_chain()` / `log_event()` — Global singleton + convenience API

### chain_manager.py — Namespace Chain Manager
- `ChainManager` — Per-namespace chain isolation, lazy creation, DB state restore
- `get_chain_manager()` — Global singleton

### auth.py — API Key Authentication
- `AuthContext` — Dataclass: namespace_id, namespace_name, mode
- `require_auth()` — FastAPI dependency, validates Bearer tokens
- `bootstrap_namespace()` — Create namespace + API key pair
- Key format: `etch_{live|test}_sk_{token}`, stored as SHA-256 hash

### models.py — ORM (4 tables)
- `ProofRecord` (etch_proofs) — Legacy proof storage
- `Namespace` (etch_namespaces) — Multi-tenant isolation
- `ApiKey` (etch_api_keys) — Hashed API keys with mode
- `RecordEntry` (etch_records) — SoR record entries

### assent_api.py — Assent Events
- `assent_router` — Public event stamping + chain reads
- `_SlidingWindowLimiter`, `_client_ip` — Shared rate-limit primitives (reused by assent_docs_api)
- `ensure_assent_namespace()` — Bootstrap `assent/public` namespace on lifespan

### assent_docs_api.py — Encrypted Document Store (V2)
- `assent_docs_router` — Opaque ciphertext upload/download/replace
- File-backed by default; IO narrowed to `_read`/`_write` for R2 migration
- Separate per-IP sliding window (10 uploads/hr) — uploads heavier than stamps

### glyph.py — Shortcodes + Sigils
- `encode_shortcode()` / `decode_shortcode()` / `verify_shortcode()` — Crockford base32
- `render_bar_sigil()` — Spotify-code-style horizontal bars (PNG)
- `render_qr_sigil()` — Standard QR for video corners (needs the `qr` extra)
- Alphabet excludes I/L/O/U so humans and OCR don't confuse characters

### watermark/ — etchmark
- `payload.py` — Bit packing, CRC-12, sync pattern (`PAYLOAD_BITS`, `SYNC_PATTERN`)
- `sync.py` — Chunk alignment / sync recovery
- `spread.py` — Spread-spectrum FFT layer
- `io.py` — `read_audio()` (WAV/FLAC native, else ffmpeg), `write_wav()`, `AudioIOError`
- `pipeline.py` — `embed()` / `extract()`
- Pointer, not proof: the chain stays the source of truth; the public PRNG seed is intentional

### dooh/ — DOOH Receipt SDK (Apache-2.0)
- `KeyPair` — ed25519 generate/save/load/sign; `verify_signature()`
- `Receipt`, `SignedBundle`, `sign_receipt()`, `countersign()` — bilateral signing
- `IdentityManifest` — advertiser-published `key_id → public key` map; no global PKI
- `EtchSubmitter` — sync httpx client, stored + hash-only modes
- `verify_bundle()` — six-step offline verification
- `canonical.py` — RFC 8785 (JCS) for deterministic signing bytes

### sdk.py — Python SDK
- `EtchClient(base_url, api_key)` — Async context manager
- Legacy: `register()`, `verify()` (deprecated)
- v2: `records.create(data=, record_type=, record_id=, metadata=, if_changed=)`, `records.verify()`, `records.list()`, `records.proof()`, `records.stats()`, `chain.root()`

## Assent Frontend (assent-app/)

- **Stack**: React 18 + Vite 5 + TypeScript + Tailwind + react-router-dom 6
- **Deps of note**: `pdf-lib` (write), `pdfjs-dist` (render), `qrcode` (send-link QR)
- **Routes**: `/` (Home), `/sign` (place → review → finalize), `/send` (send-to-sign, V2), `/verify`
- **hooks/useDraggableResizable.ts** — shared drag + resize for signature, text, and stamp overlays
- **components/StampFieldOverlay.tsx** — the QR verification stamp: movable, resizable, removable
- **lib/crypto.ts** — WebCrypto AES-256-GCM: `generateKey`, encrypt/decrypt, `[IV (12) || CT]` layout, base64url export for URL fragment
- **lib/etch.ts** — Browser client for `/v1/assent/*`
- **lib/pdf.ts** — pdf-lib writes + PDF.js rendering
- **lib/routing.ts** — URL fragment parsing (`#key=<b64>&t=<write_token>`)
- **lib/handoff.ts** — module-scoped `Map` staging Home → Sign; avoids the ~60 MB cost of
  round-tripping a 10 MB `Uint8Array` through `sessionStorage`
- Build output: `site/assent/` (deployed behind `deploy/Caddyfile.assent`)

## Configuration

- `pyproject.toml` — Build (hatchling), deps, pytest (asyncio_mode=auto), ruff (py311, 120 chars)
- Env: `ETCH_DATABASE_URL` / `DATABASE_URL` (default: `sqlite+aiosqlite:///./etch.db`)
- Env: `ETCH_LOG_LEVEL` (default: `INFO`) — uvicorn configures only its own loggers, so
  `server.configure_logging()` gives the `etch` loggers a handler
- Env: `ETCH_ASSENT_DOC_DIR` (default: `/var/etch/assent-documents`)
- Env: `ETCH_ASSENT_IP_SALT` (default: `etch-assent`) — salts hashed client IPs

## Dependencies

- Core: fastapi >=0.111, uvicorn[standard] >=0.29, sqlalchemy >=2.0, aiosqlite >=0.20, pydantic >=2.5, httpx >=0.27, python-multipart >=0.0.9
- DOOH: cryptography >=42 (ed25519), rfc8785 >=0.1 (JCS canonicalization)
- Glyph/watermark: Pillow >=10, numpy >=2.0, soundfile >=0.12; `ffmpeg` on PATH for lossy formats
- Optional: `[postgres]` asyncpg >=0.29 · `[qr]` qrcode[pil] >=7.4 · `[all]`
- Dev: pytest, pytest-asyncio, ruff, mypy

## Production

- **API**: https://etch.locker (Caddy reverse proxy, auto-TLS)
- **Assent SPA**: served from `site/assent/` behind `deploy/Caddyfile.assent`
- **Static site**: landing page served by Caddy
- **DB**: PostgreSQL container

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn etch.server:app --reload

# Assent frontend
cd assent-app && npm install && npm run dev
```
