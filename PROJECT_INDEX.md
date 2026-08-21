# Project Index: Etch

Generated: 2026-04-24

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
│   ├── sdk.py               # Async Python SDK (EtchClient)
│   └── server.py            # FastAPI app entrypoint, lifespan, /health
├── assent-app/              # React+Vite frontend for Etch Assent (builds → site/assent/)
│   ├── src/routes/          # Home, Sign, Send, Verify
│   ├── src/components/      # PdfViewer, SignatureField, SignaturePad, TextFieldOverlay, VerifyChain
│   └── src/lib/             # crypto (AES-GCM WebCrypto), hash, etch, pdf (pdf-lib + PDF.js), signatures, handoff, routing
├── tests/
│   ├── test_chain.py              # Unit tests: AuditChain, InclusionProof, verify
│   ├── test_api.py                # Legacy API tests (httpx + ASGI, mocked DB)
│   ├── test_sdk.py                # SDK client tests
│   ├── test_batch_api.py          # Batch registration tests
│   ├── test_c2pa.py               # C2PA compatibility tests
│   ├── test_records_api.py        # SoR API tests
│   ├── test_assent_api.py         # Etch Assent public event/chain API tests
│   └── test_assent_docs_api.py    # Encrypted document storage API tests (V2)
├── docs/
│   ├── ETCH_ASSENT_SPEC.md        # Assent product + protocol spec (V1 + V2 send-to-sign)
│   ├── dooh-spec.md               # DOOH playback receipt spec (CC-BY 4.0)
│   └── RELEASING.md               # Release procedure (PyPI via OIDC)
├── deploy/
│   ├── Caddyfile.assent    # Caddy config for assent.etch.locker (serves SPA + proxies /v1/*)
│   └── nginx.conf          # Legacy nginx config (unused in prod — Caddy serves etch.locker)
├── site/
│   ├── index.html          # Landing page (dark theme, self-contained, 762 lines)
│   └── assent/             # Built React SPA (output of `vite build` in assent-app/)
├── .github/workflows/
│   ├── ci.yml              # Test matrix: Python 3.11/3.12/3.13, ruff lint, assent-frontend (tsc + vite build)
│   └── release.yml         # PyPI publish on git tag v*
├── pyproject.toml          # Hatch build, deps, pytest/ruff config
├── Dockerfile              # Python 3.12-slim, non-root, port 8100
├── docker-compose.yml      # etch (8101) + postgres + nginx
├── README.md               # Quick start, API reference
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
- `GET /v1/chain/root` — Current chain state

### /v1/assent/* (Etch Assent — anonymous, rate-limited)
Event/chain endpoints (namespace pinned to `assent/public`, 20 events/hr per IP):
- `POST /v1/assent/stamp` — Commit an `assent.event` (created/field_added/signed/finalized)
- `GET /v1/assent/chain/{document_id}` — Fetch the event chain with integrity check
- `GET /v1/assent/records/{record_id}` — Fetch a single receipt
- `GET /v1/assent/records/{record_id}/proof` — Self-contained inclusion proof (public)
- `GET /v1/assent/verify?hash={sha256}` — Find events by document hash (recipient-side)

E2EE document storage (send-to-sign, V2 slice 1 — 10 uploads/hr per IP, 15 MB cap):
- `POST /v1/assent/document` — Upload opaque ciphertext → `{ document_id }`
- `GET  /v1/assent/document/{doc_id}` — Download ciphertext (octet-stream)
- `PUT  /v1/assent/document/{doc_id}` — Replace (signed re-upload)
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

### sdk.py — Python SDK
- `EtchClient(base_url, api_key)` — Async context manager
- Legacy: `register()`, `verify()` (deprecated)
- v2: `records.create()`, `records.verify()`

## Assent Frontend (assent-app/)

- **Stack**: React 18 + Vite 5 + TypeScript + Tailwind + react-router-dom 6
- **Deps of note**: `pdf-lib` (write), `pdfjs-dist` (render), `qrcode` (send-link QR)
- **Routes**: `/` (Home), `/sign` (local signing), `/send` (send-to-sign, V2), `/verify`
- **lib/crypto.ts** — WebCrypto AES-256-GCM: `generateKey`, encrypt/decrypt, `[IV (12) || CT]` layout, base64url export for URL fragment
- **lib/etch.ts** — Browser client for `/v1/assent/*`
- **lib/pdf.ts** — pdf-lib writes + PDF.js rendering
- **lib/routing.ts** — URL fragment key parsing (`#key=<b64>`)
- Build output: `site/assent/` (deployed behind `deploy/Caddyfile.assent`)

## Configuration

- `pyproject.toml` — Build (hatchling), deps, pytest (asyncio_mode=auto), ruff (py311, 120 chars)
- Env: `ETCH_DATABASE_URL` / `DATABASE_URL` (default: `sqlite+aiosqlite:///./etch.db`)
- Env: `ETCH_ASSENT_DOC_DIR` (default: `/var/etch/assent-documents`)

## Dependencies

- fastapi >=0.111, uvicorn[standard] >=0.29, sqlalchemy >=2.0, aiosqlite >=0.20, pydantic >=2.5, httpx >=0.27
- Optional: asyncpg >=0.29 (postgres)
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
