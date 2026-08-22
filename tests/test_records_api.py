"""Tests for the Etch SoR API — /v1/records endpoints.

Runs against the real ASGI app with in-memory SQLite, using the v2 SDK.
"""
from __future__ import annotations

import hashlib

import httpx
import pytest

from etch.sdk import (
    ChainState,
    EtchClient,
    EtchNotFoundError,
    EtchValidationError,
    InclusionProof,
    RecordReceipt,
    RecordVerifyResult,
    _hash_data,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app():
    """Create a fresh FastAPI app backed by an in-memory SQLite DB."""
    import etch.db as db_module
    import etch.chain as chain_module
    import etch.chain_manager as cm_module
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Patch DB
    orig_session_maker = db_module._session_maker
    orig_engine = db_module._engine
    db_module._session_maker = session_maker
    db_module._engine = engine

    # Reset global chain (legacy)
    orig_chain = chain_module._global_chain
    chain_module._global_chain = None

    # Reset chain manager (v2)
    orig_manager = cm_module._manager
    cm_module._manager = None

    # Reset the per-process write counters so each test sees only its own writes
    import etch.records_api as records_api_module
    records_api_module._reset_stats()

    from etch.server import app

    return app, engine, {
        "db_module": db_module,
        "chain_module": chain_module,
        "cm_module": cm_module,
        "orig_session_maker": orig_session_maker,
        "orig_engine": orig_engine,
        "orig_chain": orig_chain,
        "orig_manager": orig_manager,
    }


async def _bootstrap_ns(ns_name: str = "Test Corp", ns_id: str = "ns_test") -> str:
    """Create a namespace + API key, return the raw key."""
    from etch.auth import bootstrap_namespace
    _, raw_key = await bootstrap_namespace(ns_name, namespace_id=ns_id)
    return raw_key


@pytest.fixture
async def client():
    """Yield an EtchClient wired to the ASGI test app with a bootstrapped namespace."""
    app, engine, originals = _make_app()

    from etch.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Bootstrap a namespace
    api_key = await _bootstrap_ns()

    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    etch_client = EtchClient(httpx_client=http_client, api_key=api_key)

    yield etch_client

    await etch_client.close()
    await http_client.aclose()

    # Restore originals
    originals["db_module"]._session_maker = originals["orig_session_maker"]
    originals["db_module"]._engine = originals["orig_engine"]
    originals["chain_module"]._global_chain = originals["orig_chain"]
    originals["cm_module"]._manager = originals["orig_manager"]


# ---------------------------------------------------------------------------
# POST /v1/records
# ---------------------------------------------------------------------------

class TestCreateRecord:

    async def test_create_with_data(self, client: EtchClient):
        data = {"stage": "Closed Won", "amount": 150000}
        receipt = await client.records.create(
            data=data,
            record_type="salesforce.opportunity",
            record_id="opp-001",
            metadata={"actor": "agent:sales-bot", "action": "field_update"},
        )
        assert isinstance(receipt, RecordReceipt)
        assert receipt.id.startswith("rec_")
        assert receipt.record_hash == _hash_data(data)
        assert receipt.namespace == "ns_test"
        assert receipt.record_type == "salesforce.opportunity"
        assert receipt.chain_position == 0
        assert receipt.chain_depth == 1
        assert len(receipt.leaf_hash) == 64
        assert len(receipt.mmr_root) == 64

    async def test_create_with_hash(self, client: EtchClient):
        h = hashlib.sha256(b"pre-hashed").hexdigest()
        receipt = await client.records.create(record_hash=h)
        assert receipt.record_hash == h

    async def test_create_no_data_no_hash_raises(self, client: EtchClient):
        with pytest.raises(EtchValidationError):
            await client.records.create()

    async def test_chain_position_increments(self, client: EtchClient):
        r1 = await client.records.create(data={"a": 1})
        r2 = await client.records.create(data={"b": 2})
        assert r2.chain_position == r1.chain_position + 1
        assert r2.chain_depth == r1.chain_depth + 1

    async def test_mmr_root_changes(self, client: EtchClient):
        r1 = await client.records.create(data={"a": 1})
        r2 = await client.records.create(data={"b": 2})
        assert r1.mmr_root != r2.mmr_root

    async def test_metadata_round_trips(self, client: EtchClient):
        meta = {"actor": "agent:test", "source": "pytest", "custom_field": 42}
        receipt = await client.records.create(data={"x": 1}, metadata=meta)
        # Retrieve and check metadata is stored
        retrieved = await client.records.retrieve(receipt.id)
        assert retrieved.metadata == meta


# ---------------------------------------------------------------------------
# POST /v1/records — if_changed
# ---------------------------------------------------------------------------

class TestIfChanged:

    async def test_unchanged_content_is_deduplicated(self, client: EtchClient):
        data = {"target": "ENSG00000141510", "score": 0.91}
        first = await client.records.create(
            data=data, record_type="opentargets.association", record_id="assoc-1",
        )
        second = await client.records.create(
            data=data, record_type="opentargets.association", record_id="assoc-1",
            if_changed=True,
        )
        assert second.deduplicated is True
        assert second.id == first.id
        assert second.chain_position == first.chain_position
        assert second.mmr_root == first.mmr_root

    async def test_dedup_does_not_grow_the_chain(self, client: EtchClient):
        data = {"target": "ENSG1", "score": 0.5}
        await client.records.create(data=data, record_id="assoc-2", if_changed=True)
        before = await client.chain.root()
        for _ in range(5):
            await client.records.create(data=data, record_id="assoc-2", if_changed=True)
        after = await client.chain.root()
        assert after.chain_depth == before.chain_depth
        assert after.mmr_root == before.mmr_root

    async def test_changed_content_appends(self, client: EtchClient):
        first = await client.records.create(
            data={"score": 0.5}, record_id="assoc-3", if_changed=True,
        )
        second = await client.records.create(
            data={"score": 0.7}, record_id="assoc-3", if_changed=True,
        )
        assert second.deduplicated is False
        assert second.id != first.id
        assert second.chain_position == first.chain_position + 1

    async def test_first_write_appends(self, client: EtchClient):
        receipt = await client.records.create(
            data={"score": 0.5}, record_id="assoc-4", if_changed=True,
        )
        assert receipt.deduplicated is False
        assert receipt.chain_position == 0

    async def test_default_still_appends_unchanged_content(self, client: EtchClient):
        """Re-attestation stays the default: no silent deduplication."""
        data = {"score": 0.5}
        first = await client.records.create(data=data, record_id="assoc-5")
        second = await client.records.create(data=data, record_id="assoc-5")
        assert second.deduplicated is False
        assert second.id != first.id
        assert second.chain_position == first.chain_position + 1

    async def test_no_external_id_always_appends(self, client: EtchClient):
        data = {"score": 0.5}
        first = await client.records.create(data=data, if_changed=True)
        second = await client.records.create(data=data, if_changed=True)
        assert second.deduplicated is False
        assert second.chain_position == first.chain_position + 1

    async def test_same_external_id_different_type_appends(self, client: EtchClient):
        data = {"score": 0.5}
        first = await client.records.create(
            data=data, record_type="a.thing", record_id="shared-id", if_changed=True,
        )
        second = await client.records.create(
            data=data, record_type="b.thing", record_id="shared-id", if_changed=True,
        )
        assert second.deduplicated is False
        assert second.chain_position == first.chain_position + 1

    async def test_deduplicated_receipt_still_verifies(self, client: EtchClient):
        data = {"score": 0.5}
        await client.records.create(data=data, record_id="assoc-6", if_changed=True)
        dedup = await client.records.create(data=data, record_id="assoc-6", if_changed=True)
        result = await client.records.verify(dedup.id, data=data)
        assert result.verified is True

    async def test_dedup_returns_original_metadata(self, client: EtchClient):
        data = {"score": 0.5}
        await client.records.create(
            data=data, record_id="assoc-7", metadata={"run": "first"}, if_changed=True,
        )
        dedup = await client.records.create(
            data=data, record_id="assoc-7", metadata={"run": "second"}, if_changed=True,
        )
        assert dedup.deduplicated is True
        assert dedup.metadata == {"run": "first"}


# ---------------------------------------------------------------------------
# GET /v1/records/{record_id}
# ---------------------------------------------------------------------------

class TestRetrieveRecord:

    async def test_retrieve_existing(self, client: EtchClient):
        receipt = await client.records.create(data={"test": True}, record_type="unit.test")
        retrieved = await client.records.retrieve(receipt.id)
        assert retrieved.id == receipt.id
        assert retrieved.record_hash == receipt.record_hash
        assert retrieved.record_type == "unit.test"

    async def test_retrieve_not_found(self, client: EtchClient):
        with pytest.raises(EtchNotFoundError):
            await client.records.retrieve("rec_doesnotexist")


# ---------------------------------------------------------------------------
# GET /v1/records/{record_id}/proof
# ---------------------------------------------------------------------------

class TestRecordProof:

    async def test_proof_structure(self, client: EtchClient):
        receipt = await client.records.create(data={"proof": "test"})
        proof = await client.records.proof(receipt.id)

        assert isinstance(proof, InclusionProof)
        assert proof.record_id == receipt.id
        assert proof.leaf_hash == receipt.leaf_hash
        assert proof.mmr_root == receipt.mmr_root
        assert proof.algorithm == "sha256"
        assert len(proof.verification_steps) == 2

    async def test_proof_offline_verifiable(self, client: EtchClient):
        """Verify the proof can be checked with just SHA-256."""
        receipt = await client.records.create(data={"verify": "offline"})
        proof = await client.records.proof(receipt.id)

        # Recompute leaf_hash
        leaf_content = f"{proof.prev_root}:record_commit:{proof.payload_hash}:{proof.timestamp}"
        expected_leaf = hashlib.sha256(leaf_content.encode()).hexdigest()
        assert expected_leaf == proof.leaf_hash

        # Recompute mmr_root
        expected_root = hashlib.sha256(f"{proof.prev_root}:{proof.leaf_hash}".encode()).hexdigest()
        assert expected_root == proof.mmr_root

    async def test_proof_not_found(self, client: EtchClient):
        with pytest.raises(EtchNotFoundError):
            await client.records.proof("rec_doesnotexist")


# ---------------------------------------------------------------------------
# POST /v1/records/verify
# ---------------------------------------------------------------------------

class TestVerifyRecord:

    async def test_verify_correct_data(self, client: EtchClient):
        data = {"stage": "won", "amount": 100}
        receipt = await client.records.create(data=data)
        result = await client.records.verify(receipt.id, data=data)

        assert isinstance(result, RecordVerifyResult)
        assert result.content_match is True
        assert result.chain_integrity is True
        assert result.verified is True

    async def test_verify_wrong_data(self, client: EtchClient):
        receipt = await client.records.create(data={"original": True})
        result = await client.records.verify(receipt.id, data={"tampered": True})

        assert result.content_match is False
        assert result.verified is False

    async def test_verify_with_hash(self, client: EtchClient):
        data = {"check": "hash"}
        receipt = await client.records.create(data=data)
        result = await client.records.verify(receipt.id, record_hash=_hash_data(data))
        assert result.verified is True

    async def test_verify_not_found(self, client: EtchClient):
        with pytest.raises(EtchNotFoundError):
            await client.records.verify("rec_nope", data={"x": 1})


# ---------------------------------------------------------------------------
# GET /v1/records (list)
# ---------------------------------------------------------------------------

class TestListRecords:

    async def test_list_returns_records(self, client: EtchClient):
        await client.records.create(data={"a": 1}, record_type="test.type")
        await client.records.create(data={"b": 2}, record_type="test.type")
        await client.records.create(data={"c": 3}, record_type="other.type")

        records = await client.records.list()
        assert len(records) == 3

    async def test_list_filter_by_type(self, client: EtchClient):
        await client.records.create(data={"a": 1}, record_type="crm.deal")
        await client.records.create(data={"b": 2}, record_type="crm.contact")

        records = await client.records.list(type="crm.deal")
        assert len(records) == 1
        assert records[0].record_type == "crm.deal"

    async def test_list_newest_first(self, client: EtchClient):
        await client.records.create(data={"first": True})
        await client.records.create(data={"second": True})

        records = await client.records.list()
        assert records[0].chain_position > records[1].chain_position


# ---------------------------------------------------------------------------
# GET /v1/chain/root
# ---------------------------------------------------------------------------

class TestChainRoot:

    async def test_chain_root(self, client: EtchClient):
        state = await client.chain.root()
        assert isinstance(state, ChainState)
        assert state.namespace == "ns_test"
        assert len(state.mmr_root) == 64
        assert state.chain_depth == 0

    async def test_chain_root_updates_after_record(self, client: EtchClient):
        state1 = await client.chain.root()
        await client.records.create(data={"x": 1})
        state2 = await client.chain.root()

        assert state2.chain_depth == state1.chain_depth + 1
        assert state2.mmr_root != state1.mmr_root


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:

    async def test_no_auth_returns_422(self):
        """Requests without Authorization header get rejected."""
        app, engine, originals = _make_app()

        from etch.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            resp = await http.post("/v1/records", json={"record": {"data": {"a": 1}}})
            assert resp.status_code == 422  # Missing required header

        originals["db_module"]._session_maker = originals["orig_session_maker"]
        originals["db_module"]._engine = originals["orig_engine"]
        originals["chain_module"]._global_chain = originals["orig_chain"]
        originals["cm_module"]._manager = originals["orig_manager"]

    async def test_bad_key_returns_401(self):
        """Requests with invalid API key get rejected."""
        app, engine, originals = _make_app()

        from etch.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            resp = await http.post(
                "/v1/records",
                json={"record": {"data": {"a": 1}}},
                headers={"Authorization": "Bearer etch_live_sk_invalid_key_here"},
            )
            assert resp.status_code == 401

        originals["db_module"]._session_maker = originals["orig_session_maker"]
        originals["db_module"]._engine = originals["orig_engine"]
        originals["chain_module"]._global_chain = originals["orig_chain"]
        originals["cm_module"]._manager = originals["orig_manager"]


# ---------------------------------------------------------------------------
# Client-side hashing (privacy)
# ---------------------------------------------------------------------------

class TestPrivacy:

    async def test_data_hashed_client_side(self, client: EtchClient):
        """The SDK should hash data before sending — server never sees raw data."""
        data = {"secret": "password123", "ssn": "123-45-6789"}
        receipt = await client.records.create(data=data)
        assert receipt.record_hash == _hash_data(data)


# ---------------------------------------------------------------------------
# GET /v1/records/stats — dedup observability
# ---------------------------------------------------------------------------

class TestRecordsStats:

    async def test_counts_appends(self, client: EtchClient):
        for i in range(3):
            await client.records.create(data={"n": i}, record_id=f"r-{i}")
        stats = await client.records.stats()
        assert stats.appended == 3
        assert stats.deduplicated == 0
        assert stats.records_total == 3

    async def test_counts_dedups_separately(self, client: EtchClient):
        data = {"target": "ENSG1", "score": 0.5}
        await client.records.create(data=data, record_id="assoc-1", if_changed=True)
        for _ in range(2):
            await client.records.create(data=data, record_id="assoc-1", if_changed=True)
        stats = await client.records.stats()
        assert stats.appended == 1
        assert stats.deduplicated == 2
        # The chain only grew once; the durable row count agrees with appended.
        assert stats.records_total == 1

    async def test_dedup_rate(self, client: EtchClient):
        data = {"a": 1}
        await client.records.create(data=data, record_id="x", if_changed=True)
        await client.records.create(data=data, record_id="x", if_changed=True)
        stats = await client.records.stats()
        assert stats.dedup_rate == 0.5

    async def test_dedup_rate_is_zero_when_nothing_written(self, client: EtchClient):
        stats = await client.records.stats()
        assert stats.appended == 0
        assert stats.deduplicated == 0
        assert stats.dedup_rate == 0.0
        assert stats.records_total == 0

    async def test_route_not_shadowed_by_record_id(self, client: EtchClient):
        """/v1/records/stats must not be parsed as a record_id lookup."""
        await client.records.create(data={"a": 1})
        resp = await client._client.get(
            "/v1/records/stats",
            headers=client._auth_headers(),
        )
        assert resp.status_code == 200
        assert "appended" in resp.json()

    async def test_is_namespace_scoped(self, client: EtchClient):
        """Another tenant's writes must not show up in this namespace's counters."""
        other_key = await _bootstrap_ns("Other Corp", ns_id="ns_other")
        other = EtchClient(httpx_client=client._client, api_key=other_key)

        await client.records.create(data={"a": 1}, record_id="mine")
        await other.records.create(data={"b": 2}, record_id="theirs")
        await other.records.create(data={"b": 2}, record_id="theirs", if_changed=True)

        mine = await client.records.stats()
        theirs = await other.records.stats()

        assert (mine.appended, mine.deduplicated) == (1, 0)
        assert (theirs.appended, theirs.deduplicated) == (1, 1)
        assert mine.namespace == "ns_test"
        assert theirs.namespace == "ns_other"

    async def test_requires_auth(self, client: EtchClient):
        resp = await client._client.get("/v1/records/stats")
        assert resp.status_code in (401, 422)

    async def test_dedup_is_logged(self, client: EtchClient, caplog):
        """The dedup event must reach the log — it is invisible in the row count."""
        import logging
        data = {"a": 1}
        await client.records.create(data=data, record_id="logged", if_changed=True)
        with caplog.at_level(logging.INFO, logger="etch.records_api"):
            await client.records.create(data=data, record_id="logged", if_changed=True)
        assert any("deduplicated" in r.message for r in caplog.records)
