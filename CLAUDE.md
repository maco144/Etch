# CLAUDE.md — Etch

## What is Etch?

Content provenance on a tamper-evident Merkle chain. Register, verify, and prove content existed at a point in time. Privacy-preserving (content never stored, only SHA-256 hashes).

## Quick Reference

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Run server (default port 8100)
uvicorn etch.server:app --reload

# Lint
ruff check etch/ tests/
```

## Architecture

- **chain.py** — Core: MMR hash chain (`AuditChain`), inclusion proofs, offline verification
- **chain_manager.py** — Namespace-isolated chain manager (per-tenant chains for SoR API)
- **api.py** — FastAPI router at `/v1/proof/*` (legacy: register, verify, lookup, batch, stats)
- **records_api.py** — FastAPI router at `/v1/records/*` (SoR API, namespace-isolated, API key auth)
- **c2pa.py** — FastAPI router at `/v1/c2pa/*` (C2PA manifest bridge for EU AI Act compliance)
- **auth.py** — API key authentication (`etch_{mode}_sk_{token}`), namespace bootstrap
- **models.py** — SQLAlchemy ORM: `ProofRecord`, `Namespace`, `ApiKey`, `RecordEntry` (4 tables)
- **db.py** — Async DB sessions (SQLite default, PostgreSQL via `ETCH_DATABASE_URL`)
- **sdk.py** — Async Python SDK (`EtchClient`) with legacy + v2 API support
- **server.py** — FastAPI app with lifespan (auto-creates tables on startup)

## Key Patterns

- Global singleton chain via `get_chain()` / `log_event()` in chain.py
- Thread-safe chain operations (threading.Lock)
- Persist hook pattern: chain calls sync hook after append (outside lock)
- API tests use httpx ASGITransport with mocked DB layer
- `asyncio_mode = "auto"` in pytest config
- The chain is append-only and every root hashes the previous one — nothing is ever deleted or compacted, so write-amplification is permanent. A write-only API invites clients to re-register their whole working set every run; `POST /v1/records` takes `if_changed: true` (with `record.id`) to make that cheap: one indexed lookup on `idx_records_ns_ext`, and an unchanged `record_hash` returns the existing receipt with `deduplicated: true` instead of appending. It is opt-in on purpose — re-registering unchanged content is a legitimate timestamped re-attestation, so the default must never silently deduplicate.

## Environment Variables

- `ETCH_DATABASE_URL` or `DATABASE_URL` — DB connection string (default: `sqlite+aiosqlite:///./etch.db`)

## Conventions

- Python 3.11+, ruff line-length 120
- License: Rising Sun License v1.0
