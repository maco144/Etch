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

---

## AIOS Portfolio Integration

Etch is part of the **Rising Sun** portfolio managed by the AIOS autonomous CEO engine at `eudaimonia.win`. Work items (research, content, infra tasks) are dispatched here from the CEO cycle.

### Work Queue

Pending tasks live on the AIOS rising server. Fetch at session start:

```bash
# List pending tasks
curl http://eudaimonia.win:8000/api/v1/portfolio/companies/etch/work?status=pending&limit=50

# Compact view
curl -s 'http://eudaimonia.win:8000/api/v1/portfolio/companies/etch/work?status=pending&limit=50' \
  | python3 -c "import sys,json; [print(i['id'][:8], i['category'], '-', i['title'][:80]) for i in json.load(sys.stdin)['items']]"

# Mark a task done
curl -X POST "http://eudaimonia.win:8000/api/v1/company/work-queue/{item_id}/complete?note=what+was+done"
```

Work through tasks one at a time. Mark done immediately after each one is complete.

### Shared Work Queue (PostgreSQL)

This project's work queue (`company_id="etch"`) lives in the **shared PostgreSQL** on rising — not a local SQLite file and not behind the kernel REST API at `:8000`.

**Check pending tasks:**
```bash
ssh rising "docker exec eudaimonia-eudaimonia-postgres-1 psql -U eudaimonia -c \
  \"SELECT id, title, status, priority FROM work_items WHERE company_id='etch' AND status='pending' ORDER BY priority DESC\""
```

**Mark a task done:**
```bash
ssh rising "docker exec eudaimonia-eudaimonia-postgres-1 psql -U eudaimonia -c \
  \"UPDATE work_items SET status='done', completion_note='<note>' WHERE id='<uuid>'\""
```

**Do NOT** rely on `http://eudaimonia.win:8000` for work queue access — the kernel restarts frequently during upgrades and the API will timeout. Use PostgreSQL directly.

### Rising (Production)

- **AIOS API:** `http://eudaimonia.win:8000`
- **Portfolio config:** `companies/etch/company.toml` in the AIOS repo
- **Codebase path used by CEO cycle:** `/home/alex/etch`
