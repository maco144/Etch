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
- **api.py** — FastAPI router at `/v1/proof/*` (register, verify, lookup, stats)
- **models.py** — SQLAlchemy `ProofRecord` table
- **db.py** — Async DB sessions (SQLite default, PostgreSQL via `ETCH_DATABASE_URL`)
- **server.py** — FastAPI app with lifespan (auto-creates tables on startup)

## Key Patterns

- Global singleton chain via `get_chain()` / `log_event()` in chain.py
- Thread-safe chain operations (threading.Lock)
- Persist hook pattern: chain calls sync hook after append (outside lock)
- API tests use httpx ASGITransport with mocked DB layer
- `asyncio_mode = "auto"` in pytest config

## Environment Variables

- `ETCH_DATABASE_URL` or `DATABASE_URL` — DB connection string (default: `sqlite+aiosqlite:///./etch.db`)

## Conventions

- Python 3.11+, ruff line-length 120
- License: FSL-1.1-Apache-2.0
