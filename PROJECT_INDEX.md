# Project Index: Etch

Generated: 2026-03-27

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
│   ├── sdk.py               # Async Python SDK (EtchClient)
│   └── server.py            # FastAPI app entrypoint, lifespan, /health
├── tests/
│   ├── test_chain.py        # Unit tests: AuditChain, InclusionProof, verify
│   ├── test_api.py          # Legacy API tests (httpx + ASGI, mocked DB)
│   ├── test_sdk.py          # SDK client tests
│   ├── test_batch_api.py    # Batch registration tests
│   ├── test_c2pa.py         # C2PA compatibility tests
│   └── test_records_api.py  # SoR API tests
├── docs/
│   ├── eu-ai-act-prospects.md   # EU AI Act Article 50 compliance research
│   ├── prospect-pipeline.md     # Business integration prospects
│   ├── licensing-model.md       # License tiers, Nous network integration
│   └── RELEASING.md             # Release procedure (PyPI via OIDC)
├── deploy/
│   └── nginx.conf           # Nginx config (unused in prod — Caddy serves etch.locker)
├── site/
│   └── index.html           # Landing page (dark theme, self-contained, 762 lines)
├── .github/workflows/
│   ├── ci.yml               # Test matrix: Python 3.11/3.12/3.13, ruff lint
│   └── release.yml          # PyPI publish on git tag v*
├── pyproject.toml           # Hatch build, deps, pytest/ruff config
├── Dockerfile               # Python 3.12-slim, non-root, port 8100
├── docker-compose.yml       # etch (8101) + postgres + nginx
├── README.md                # Quick start, API reference
└── LICENSE.md               # Rising Sun License v1.0
```

## Entry Points

- **Server**: `etch/server.py` — `uvicorn etch.server:app --reload` (port 8100)
- **Library**: `from etch import AuditChain, verify_inclusion_proof, EtchClient`
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
- `POST /v1/records` — Create record receipt
- `GET /v1/records` — List/filter with cursor pagination
- `GET /v1/records/{record_id}` — Retrieve receipt
- `GET /v1/records/{record_id}/proof` — Self-contained inclusion proof
- `POST /v1/records/verify` — Verify record against chain
- `GET /v1/chain/root` — Current chain state

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

### sdk.py — Python SDK
- `EtchClient(base_url, api_key)` — Async context manager
- Legacy: `register()`, `verify()` (deprecated)
- v2: `records.create()`, `records.verify()`

## Configuration

- `pyproject.toml` — Build (hatchling), deps, pytest (asyncio_mode=auto), ruff (py311, 120 chars)
- Env: `ETCH_DATABASE_URL` or `DATABASE_URL` (default: sqlite+aiosqlite:///./etch.db)

## Dependencies

- fastapi >=0.111, uvicorn[standard] >=0.29, sqlalchemy >=2.0, aiosqlite >=0.20, pydantic >=2.5, httpx >=0.27
- Optional: asyncpg >=0.29 (postgres)
- Dev: pytest, pytest-asyncio, ruff, mypy

## Production (rising server)

- **URL**: https://etch.locker (Caddy reverse proxy, auto-TLS)
- **Container**: etch-etch-1 (port 8101→8100) + etch-postgres-1
- **Static site**: /opt/etch/site/index.html served by Caddy
- **API proxy**: /v1/*, /health, /docs → localhost:8101

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn etch.server:app --reload
```
