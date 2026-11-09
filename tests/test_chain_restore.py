"""The legacy global chain (/v1/proof, /v1/c2pa) survives a restart.

It used to live only in memory: after a restart the next write got leaf 0 again, collided with the
unique etch_proofs.leaf_index, failed to persist, and the caller still got a receipt.
"""
from __future__ import annotations

import importlib

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from etch.chain import AuditChain, InclusionProof, verify_inclusion_proof
from etch.models import ProofRecord

C2PA = {"content_hash": "c" * 64, "claim": {"claim_generator": "pytest",
                                             "assertions": [{"label": "c2pa.created", "data": {}}]}}


@pytest.fixture
async def http(tmp_path, monkeypatch):
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
    import etch.server as server_mod
    importlib.reload(server_mod)
    await db_module.create_tables()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server_mod.app), base_url="http://t") as c:
        yield c, engine
    db_module._session_maker, db_module._engine, chain_module._global_chain, cm_module._manager = orig
    await engine.dispose()


async def _restart():
    """What a container restart does to process state, then what lifespan now does about it."""
    import etch.chain as chain_module
    from etch.chain_manager import restore_global_chain
    chain_module._global_chain = None
    await restore_global_chain()


async def _rows(engine) -> list[ProofRecord]:
    async with engine.connect() as conn:
        return (await conn.execute(select(ProofRecord).order_by(ProofRecord.leaf_index))).all()


async def test_writes_after_restart_continue_the_chain(http):
    client, engine = http
    for i in range(2):
        assert (await client.post("/v1/proof", json={"content": f"before {i}"})).status_code == 200
    before = await _rows(engine)

    await _restart()
    stats = (await client.get("/v1/proof/stats")).json()
    assert stats["chain_depth"] == 2 and stats["mmr_root"] == before[-1].mmr_root

    res = await client.post("/v1/proof", json={"content": "after restart"})
    assert res.status_code == 200, res.text
    receipt = res.json()
    assert receipt["proof_id"] == 2

    rows = await _rows(engine)
    assert [r.leaf_index for r in rows] == [0, 1, 2]  # persisted, not a silent unique-constraint failure
    # ...and chained to the pre-restart leaf, not to a fresh genesis root.
    leaf = InclusionProof(leaf_index=2, leaf_hash=rows[2].leaf_hash, mmr_root=rows[2].mmr_root,
                          prev_root=before[-1].mmr_root, action_type="content_proof",
                          payload_hash=rows[2].payload_hash, timestamp=rows[2].created_at_exact)
    assert verify_inclusion_proof(leaf)


async def test_restart_on_an_empty_database_starts_at_genesis(http):
    client, _ = http
    await _restart()
    assert (await client.get("/v1/proof/stats")).json()["chain_depth"] == 0
    assert (await client.post("/v1/proof", json={"content": "first"})).json()["proof_id"] == 0


async def test_c2pa_manifest_proof_survives_restart(http):
    client, engine = http
    assert (await client.post("/v1/proof", json={"content": "leaf 0"})).status_code == 200
    claim = (await client.post("/v1/c2pa/manifest", json=C2PA)).json()["claim_id"]

    await _restart()
    proof = (await client.get(f"/v1/c2pa/manifest/{claim}")).json()["inclusion_proof"]
    assert proof, "inclusion proof was empty after restart"
    rows = await _rows(engine)
    assert proof["prev_root"] == rows[0].mmr_root
    assert verify_inclusion_proof(InclusionProof(**{k: proof[k] for k in (
        "leaf_index", "leaf_hash", "mmr_root", "prev_root", "action_type", "payload_hash", "timestamp")}))


async def test_restore_refuses_to_guess_when_the_database_is_unreachable(http, monkeypatch):
    # Starting at genesis on a DB error is exactly the original bug; the server must not come up.
    import etch.chain_manager as cm_module

    def broken_session():
        raise RuntimeError("db down")
    monkeypatch.setattr(cm_module, "get_session", broken_session)
    with pytest.raises(RuntimeError):
        await _restart()


def test_generate_proof_after_restore_uses_absolute_leaf_indexes():
    chain = AuditChain()
    chain.restore(leaf_count=5, root="ab" * 32)
    entry = chain.append("content_proof", {"n": 1})
    assert entry.leaf_index == 5
    proof = chain.generate_proof(5)
    assert proof is not None and proof.prev_root == "ab" * 32 and verify_inclusion_proof(proof)
    assert chain.generate_proof(0) is None  # leaves before the restore aren't in memory: no proof, not a wrong one


def test_restore_only_on_a_fresh_chain():
    chain = AuditChain()
    chain.append("content_proof", {"n": 1})
    with pytest.raises(RuntimeError):
        chain.restore(leaf_count=5, root="ab" * 32)


async def test_rows_count_matches_stats(http):
    client, engine = http
    await client.post("/v1/proof", json={"content": "x"})
    async with engine.connect() as conn:
        n = (await conn.execute(select(func.count()).select_from(ProofRecord))).scalar()
    await _restart()
    assert (await client.get("/v1/proof/stats")).json()["chain_depth"] == n
