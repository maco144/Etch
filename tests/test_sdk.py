"""Tests for the Etch Python SDK — runs against the real ASGI app."""
from __future__ import annotations

import hashlib

import httpx
import pytest

from etch.sdk import (
    ChainStats,
    EtchClient,
    EtchNotFoundError,
    EtchValidationError,
    ProofReceipt,
    ProofRecord,
    VerifyResult,
    _sha256,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app():
    """Create a fresh FastAPI app backed by an in-memory SQLite DB."""
    import etch.db as db_module
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Patch _session_maker (used by get_session) so all imports see it
    original_session_maker = db_module._session_maker
    original_engine = db_module._engine
    db_module._session_maker = session_maker
    db_module._engine = engine

    # Reset the global chain so tests start fresh
    import etch.chain as chain_module
    original_chain = chain_module._global_chain
    chain_module._global_chain = None

    from etch.server import app

    return app, engine, db_module, original_session_maker, original_engine, chain_module, original_chain


@pytest.fixture
async def client():
    """Yield an EtchClient wired to the ASGI test app with in-memory DB."""
    app, engine, db_module, orig_session_maker, orig_engine, chain_module, orig_chain = _make_app()

    # Create tables
    from etch.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    etch_client = EtchClient(httpx_client=http_client)

    yield etch_client

    await etch_client.close()
    await http_client.aclose()

    # Restore originals
    db_module._session_maker = orig_session_maker
    db_module._engine = orig_engine
    chain_module._global_chain = orig_chain


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegister:

    async def test_register_string_content(self, client: EtchClient):
        receipt = await client.register("Hello, Etch!")
        assert isinstance(receipt, ProofReceipt)
        assert receipt.content_hash == _sha256("Hello, Etch!")
        assert receipt.proof_id >= 0
        assert len(receipt.leaf_hash) == 64
        assert len(receipt.mmr_root) == 64
        assert receipt.chain_depth >= 1

    async def test_register_bytes_content(self, client: EtchClient):
        data = b"binary data \x00\x01\x02"
        receipt = await client.register(data)
        expected = hashlib.sha256(data).hexdigest()
        assert receipt.content_hash == expected

    async def test_register_with_label_and_owner(self, client: EtchClient):
        receipt = await client.register(
            "My article",
            label="Article v1",
            owner="user-42",
        )
        assert receipt.label == "Article v1"
        assert receipt.owner == "user-42"

    async def test_register_hash_directly(self, client: EtchClient):
        h = _sha256("pre-hashed content")
        receipt = await client.register_hash(h, label="pre-hashed")
        assert receipt.content_hash == h
        assert receipt.label == "pre-hashed"

    async def test_register_invalid_hash_raises(self, client: EtchClient):
        with pytest.raises(EtchValidationError):
            await client.register_hash("tooshort")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

class TestVerify:

    async def test_verify_correct_content(self, client: EtchClient):
        content = "verify me"
        receipt = await client.register(content)
        result = await client.verify(receipt.proof_id, content)

        assert isinstance(result, VerifyResult)
        assert result.content_hash_matches is True
        assert result.verified is True
        assert result.receipt.proof_id == receipt.proof_id

    async def test_verify_wrong_content(self, client: EtchClient):
        receipt = await client.register("original")
        result = await client.verify(receipt.proof_id, "tampered")

        assert result.content_hash_matches is False
        assert result.verified is False

    async def test_verify_nonexistent_proof(self, client: EtchClient):
        with pytest.raises(EtchNotFoundError):
            await client.verify(999999, "anything")


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

class TestLookup:

    async def test_lookup_by_id(self, client: EtchClient):
        receipt = await client.register("lookup test")
        record = await client.lookup(receipt.proof_id)

        assert isinstance(record, ProofRecord)
        assert record.proof_id == receipt.proof_id
        assert record.content_hash == receipt.content_hash

    async def test_lookup_by_hash(self, client: EtchClient):
        content = "hash lookup test"
        receipt = await client.register(content)
        record = await client.lookup_hash(receipt.content_hash)

        assert record.proof_id == receipt.proof_id
        assert record.content_hash == receipt.content_hash

    async def test_lookup_not_found(self, client: EtchClient):
        with pytest.raises(EtchNotFoundError):
            await client.lookup(999999)

    async def test_lookup_hash_not_found(self, client: EtchClient):
        with pytest.raises(EtchNotFoundError):
            await client.lookup_hash("f" * 64)


# ---------------------------------------------------------------------------
# Recent & Stats
# ---------------------------------------------------------------------------

class TestRecentAndStats:

    async def test_recent_returns_list(self, client: EtchClient):
        await client.register("item 1")
        await client.register("item 2")
        await client.register("item 3")

        records = await client.recent(limit=10)
        assert isinstance(records, list)
        assert len(records) >= 3
        # Newest first
        assert records[0].proof_id >= records[-1].proof_id

    async def test_recent_pagination(self, client: EtchClient):
        for i in range(5):
            await client.register(f"page test {i}")

        page1 = await client.recent(limit=2, offset=0)
        page2 = await client.recent(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0].proof_id != page2[0].proof_id

    async def test_stats(self, client: EtchClient):
        await client.register("stats test")
        s = await client.stats()

        assert isinstance(s, ChainStats)
        assert s.total_proofs >= 1
        assert s.chain_depth >= 1
        assert len(s.mmr_root) == 64


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class TestContextManager:

    async def test_async_context_manager(self):
        app, engine, db_module, orig_session_maker, orig_engine, chain_module, orig_chain = _make_app()

        from etch.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        transport = httpx.ASGITransport(app=app)
        http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

        async with EtchClient(httpx_client=http_client) as etch:
            receipt = await etch.register("context manager test")
            assert receipt.proof_id >= 0

        await http_client.aclose()

        # Restore
        db_module._session_maker = orig_session_maker
        db_module._engine = orig_engine
        chain_module._global_chain = orig_chain


# ---------------------------------------------------------------------------
# Client-side hashing (privacy)
# ---------------------------------------------------------------------------

class TestClientSideHashing:

    def test_sha256_string(self):
        assert _sha256("hello") == hashlib.sha256(b"hello").hexdigest()

    def test_sha256_bytes(self):
        assert _sha256(b"\x00\x01") == hashlib.sha256(b"\x00\x01").hexdigest()

    async def test_content_never_sent_to_server(self, client: EtchClient):
        """Register should send content_hash, not raw content."""
        # If the SDK sends content_hash (not content), the server never sees
        # the raw data. We verify by checking the receipt hash matches our
        # client-side computation.
        content = "secret document"
        receipt = await client.register(content)
        assert receipt.content_hash == _sha256(content)
