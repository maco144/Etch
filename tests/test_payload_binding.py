"""Proofs bind content: payload_json is stored for every chain write, and the backfill recovers it for
rows written before it was stored.

The backfill tests write real rows through the public routers, erase payload_json, and require the
backfill to reproduce it byte for byte, which only a correct registered_at can do.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import time

import httpx
import pytest
from sqlalchemy import inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from etch import backfill_payloads as bf
from etch.chain import AuditChain, canonical_payload, verify_payload_binding
from etch.models import ProofRecord, RecordEntry


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ETCH_ASSENT_DOC_DIR", str(tmp_path / "docs"))
    import etch.chain as chain_module
    import etch.chain_manager as cm_module
    import etch.db as db_module

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    orig = (db_module._session_maker, db_module._engine, chain_module._global_chain, cm_module._manager)
    db_module._session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_module._engine = engine
    chain_module._global_chain = None
    cm_module._manager = None
    yield engine
    db_module._session_maker, db_module._engine, chain_module._global_chain, cm_module._manager = orig
    await engine.dispose()


@pytest.fixture
async def http(db):
    import etch.server as server_mod
    importlib.reload(server_mod)
    from etch.db import create_tables
    await create_tables()
    from etch.assent_api import _followon_limiter, _limiter, ensure_assent_namespace
    await ensure_assent_namespace()
    _limiter.reset()
    _followon_limiter.reset()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server_mod.app), base_url="http://t") as c:
        yield c


async def _all_payloads(engine) -> dict[str, str | None]:
    async with engine.connect() as conn:
        recs = (await conn.execute(select(RecordEntry.record_id, RecordEntry.payload_json))).all()
        proofs = (await conn.execute(select(ProofRecord.leaf_index, ProofRecord.payload_json))).all()
    return {**{r: p for r, p in recs}, **{f"proof:{i}": p for i, p in proofs}}


async def _write_one_of_each(http) -> None:
    res = await http.post("/v1/proof", json={"content": "hello", "label": "greeting", "owner": "alex"})
    assert res.status_code == 200, res.text
    res = await http.post("/v1/c2pa/manifest", json={
        "content_hash": "a" * 64, "title": "Image", "format": "image/png",
        "claim": {"claim_generator": "pytest", "assertions": [{"label": "c2pa.created", "data": {"x": 1}}]}})
    assert res.status_code == 200, res.text
    for i, kind in enumerate(["created", "signed"]):
        res = await http.post("/v1/assent/stamp", json={
            "document_id": "doc_binding_test", "event_type": kind, "document_hash": f"{i:064x}",
            "parent_hash": None if i == 0 else f"{i - 1:064x}", "event_index": i})
        assert res.status_code == 200, res.text


class TestStoredAtWriteTime:
    def test_chain_entry_carries_the_hashed_string(self):
        entry = AuditChain().append("record_commit", {"record_hash": "ab" * 32, "registered_at": time.time()})
        assert hashlib.sha256(entry.payload_json.encode()).hexdigest() == entry.payload_hash
        assert verify_payload_binding(entry.payload_json, entry.payload_hash)["record_hash"] == "ab" * 32
        assert verify_payload_binding(entry.payload_json + " ", entry.payload_hash) is None

    async def test_every_router_persists_payload_json(self, http, db):
        await _write_one_of_each(http)
        payloads = await _all_payloads(db)
        assert len(payloads) == 4 and all(payloads.values())

    async def test_c2pa_returns_binding(self, http):
        res = await http.post("/v1/c2pa/manifest", json={
            "content_hash": "b" * 64,
            "claim": {"claim_generator": "pytest", "assertions": [{"label": "c2pa.created", "data": {}}]}})
        assert res.status_code == 200, res.text
        proof = res.json()["inclusion_proof"]
        payload = verify_payload_binding(proof["payload_json"], proof["payload_hash"])
        assert payload["content_hash"] == "b" * 64


class TestBackfill:
    async def test_recovers_every_router_byte_for_byte(self, http, db):
        await _write_one_of_each(http)
        original = await _all_payloads(db)
        async with db.begin() as conn:
            await conn.execute(update(RecordEntry).values(payload_json=None))
            await conn.execute(update(ProofRecord).values(payload_json=None))

        dry = await bf.backfill(dry_run=True)
        assert dry["etch_records"].recovered == 2 and dry["etch_proofs"].recovered == 2
        assert not any((await _all_payloads(db)).values())  # dry run wrote nothing

        reports = await bf.backfill()
        assert [r.unrecovered for r in reports.values()] == [[], []]
        assert await _all_payloads(db) == original

        again = await bf.backfill()  # rerun is a no-op
        assert sum(r.scanned for r in again.values()) == 0

    async def test_unrecoverable_rows_are_reported_and_left_null(self, http, db):
        await _write_one_of_each(http)
        async with db.begin() as conn:
            await conn.execute(update(RecordEntry).values(payload_json=None, payload_hash="f" * 64))
        reports = await bf.backfill(back=0.01)
        assert reports["etch_records"].recovered == 0
        assert len(reports["etch_records"].unrecovered) == 2
        assert not any(p for k, p in (await _all_payloads(db)).items() if k.startswith("rec_"))

    def test_search_window(self):
        created = 1_762_207_200.123456
        base = {"record_hash": "cd" * 32}
        payload = canonical_payload({**base, "registered_at": created - 0.0042})
        h = hashlib.sha256(payload.encode()).hexdigest()
        assert bf.recover_payload(base, h, created) == payload
        assert bf.recover_payload(base, h, created, back=0.001) is None
        ahead = canonical_payload({**base, "registered_at": created + 0.00001})
        assert bf.recover_payload(base, hashlib.sha256(ahead.encode()).hexdigest(), created) == ahead


class TestMigration:
    async def test_adds_payload_json_to_pre_existing_tables(self, db):
        # Tables as deployed before this change: no payload_json column.
        async with db.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE etch_records (id INTEGER PRIMARY KEY, record_id VARCHAR(32), namespace_id VARCHAR(64), "
                "leaf_index INTEGER, leaf_hash VARCHAR(64), mmr_root VARCHAR(64), chain_depth INTEGER, "
                "payload_hash VARCHAR(64), record_type VARCHAR(200), external_id VARCHAR(200), "
                "record_hash VARCHAR(64), metadata_json TEXT, created_at DATETIME, created_at_exact FLOAT)"))
            await conn.execute(text("INSERT INTO etch_records (id, record_id) VALUES (1, 'rec_old')"))
        from etch.db import create_tables
        await create_tables()
        await create_tables()  # idempotent
        async with db.connect() as conn:
            for table in ("etch_records", "etch_proofs"):
                cols = await conn.run_sync(lambda c, t=table: {x["name"] for x in inspect(c).get_columns(t)})
                assert "payload_json" in cols
            assert (await conn.execute(text("SELECT record_id FROM etch_records"))).scalar() == "rec_old"


def test_canonical_form_is_unchanged():
    # Every stored payload_hash depends on this exact serialization.
    assert canonical_payload({"b": 1, "a": 1.5, "c": "x"}) == '{"a": 1.5, "b": 1, "c": "x"}'
    assert json.loads(canonical_payload({"t": 1762207200.1234567}))["t"] == 1762207200.1234567
