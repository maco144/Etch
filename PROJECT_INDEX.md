# Project Index: Etch

Generated: 2026-03-15

## Project Structure

```
etch/
├── etch/
│   ├── __init__.py       # Package exports: AuditChain, ChainEntry, InclusionProof, verify_inclusion_proof
│   ├── chain.py          # Core MMR audit chain (hash chain, proofs, verification)
│   ├── models.py         # SQLAlchemy ORM: ProofRecord table
│   ├── db.py             # Async DB session (PostgreSQL or SQLite fallback)
│   ├── api.py            # FastAPI router: /v1/proof/* endpoints
│   └── server.py         # FastAPI app entrypoint, lifespan, /health
├── tests/
│   ├── test_chain.py     # Unit tests for AuditChain, InclusionProof, verify
│   └── test_api.py       # API tests (httpx + ASGI transport, mocked DB)
├── pyproject.toml        # Hatch build, deps, pytest/ruff config
├── README.md             # Docs, quick start, API reference
└── .gitignore
```

## Entry Points

- **Server**: `etch/server.py` — `uvicorn etch.server:app --reload` (port 8100 default)
- **Library**: `etch/__init__.py` — `from etch import AuditChain, verify_inclusion_proof`
- **Tests**: `pytest` (asyncio_mode=auto)

## Core Modules

### chain.py — MMR Audit Chain
- `AuditChain` — Thread-safe in-memory hash chain with persist hooks
- `ChainEntry` — Dataclass: leaf_index, leaf_hash, mmr_root, payload_hash, action_type, specialist, agent_id, created_at
- `InclusionProof` — Offline-verifiable proof dataclass
- `verify_inclusion_proof()` — Standalone proof verification (no server trust)
- `get_chain()` / `log_event()` — Global singleton + convenience API

### api.py — REST API (FastAPI Router)
- `POST /v1/proof` — Register content (raw or pre-hashed) → ProofReceipt
- `GET /v1/proof/recent` — Paginated listing
- `GET /v1/proof/stats` — Chain statistics
- `GET /v1/proof/{proof_id}` — Lookup by ID
- `GET /v1/proof/hash/{content_hash}` — Lookup by SHA-256
- `POST /v1/proof/{proof_id}/verify` — Verify content + chain integrity

### models.py — ORM
- `ProofRecord` — Table `etch_proofs`: id, leaf_index, leaf_hash, mmr_root, payload_hash, content_hash, label, owner, proof_json, created_at

### db.py — Database
- Async SQLAlchemy engine (env: `ETCH_DATABASE_URL` or `DATABASE_URL`, default: SQLite)
- `get_session()` — Async context manager
- `create_tables()` — Dev/test table creation

## Configuration

- `pyproject.toml` — Build (hatchling), deps, pytest (asyncio_mode=auto), ruff (py311, line-length=120)
- Env vars: `ETCH_DATABASE_URL`, `DATABASE_URL`

## Dependencies

- **fastapi** >=0.111 — Web framework
- **uvicorn[standard]** >=0.29 — ASGI server
- **sqlalchemy** >=2.0 — Async ORM
- **aiosqlite** >=0.20 — SQLite async driver
- **pydantic** >=2.5 — Request/response models
- Optional: **asyncpg** >=0.29 (postgres)
- Dev: pytest, pytest-asyncio, httpx, ruff, mypy

## Tests

- `tests/test_chain.py` — 10 tests: genesis state, append, chain formation, deterministic hashing, inclusion proofs, tamper detection, verify_entry
- `tests/test_api.py` — 5 tests: register (content/hash/validation), log_event args, label+owner

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn etch.server:app --reload
```
