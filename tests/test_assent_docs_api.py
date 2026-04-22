"""Tests for the Etch Assent document store (/v1/assent/document/*)."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Route writes to a temp dir so tests don't touch /var/etch.
    monkeypatch.setenv("ETCH_ASSENT_DOC_DIR", str(tmp_path / "docs"))

    # assent_docs_api caches DOC_DIR at import time, so we re-import it after
    # setting the env var.
    import importlib
    import etch.assent_docs_api as mod
    importlib.reload(mod)

    # Reload the server so the reloaded router binds correctly.
    import etch.server as server_mod
    importlib.reload(server_mod)

    mod._upload_limiter.reset()

    # In-memory SQLite for the other routers so their tables exist during app lifespan.
    import etch.db as db_module
    import etch.chain as chain_module
    import etch.chain_manager as cm_module
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    orig = {
        "sm": db_module._session_maker,
        "eng": db_module._engine,
        "chain": chain_module._global_chain,
        "mgr": cm_module._manager,
    }
    db_module._session_maker = session_maker
    db_module._engine = engine
    chain_module._global_chain = None
    cm_module._manager = None

    from etch.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=server_mod.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    yield client, mod
    await client.aclose()

    db_module._session_maker = orig["sm"]
    db_module._engine = orig["eng"]
    chain_module._global_chain = orig["chain"]
    cm_module._manager = orig["mgr"]


# ---------------------------------------------------------------------------
# POST /v1/assent/document
# ---------------------------------------------------------------------------

class TestUpload:
    async def test_upload_roundtrip(self, http):
        client, _ = http
        ciphertext = os.urandom(4096)
        res = await client.post(
            "/v1/assent/document",
            content=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["object"] == "assent.document"
        assert body["size"] == len(ciphertext)
        doc_id = body["document_id"]
        assert len(doc_id) >= 16

        got = await client.get(f"/v1/assent/document/{doc_id}")
        assert got.status_code == 200
        assert got.content == ciphertext
        assert got.headers["content-type"] == "application/octet-stream"

    async def test_empty_body_rejected(self, http):
        client, _ = http
        res = await client.post("/v1/assent/document", content=b"")
        assert res.status_code == 422

    async def test_oversize_rejected(self, http):
        client, mod = http
        big = b"x" * (mod.MAX_DOCUMENT_BYTES + 1)
        res = await client.post("/v1/assent/document", content=big)
        assert res.status_code == 413

    async def test_rate_limit(self, http):
        client, mod = http
        for _ in range(mod.UPLOAD_MAX_REQUESTS):
            r = await client.post("/v1/assent/document", content=b"abc")
            assert r.status_code == 200
        over = await client.post("/v1/assent/document", content=b"abc")
        assert over.status_code == 429
        assert over.headers.get("retry-after") == str(mod.UPLOAD_WINDOW_SECONDS)


# ---------------------------------------------------------------------------
# GET / HEAD / PUT
# ---------------------------------------------------------------------------

class TestLookup:
    async def test_unknown_id_404(self, http):
        client, _ = http
        res = await client.get("/v1/assent/document/nope_does_not_exist")
        assert res.status_code == 404

    async def test_head_200_then_404(self, http):
        client, _ = http
        upload = await client.post("/v1/assent/document", content=b"hello")
        doc_id = upload.json()["document_id"]
        head = await client.head(f"/v1/assent/document/{doc_id}")
        assert head.status_code == 200
        assert head.headers["content-length"] == "5"

        missing = await client.head("/v1/assent/document/does-not-exist")
        assert missing.status_code == 404

    async def test_path_traversal_rejected(self, http):
        client, _ = http
        # `../` is not valid in our token alphabet. The router should 400 (or
        # 404 from the route matcher before we even check). Either is fine as
        # long as nothing escapes the doc dir.
        res = await client.get("/v1/assent/document/..%2Fetc%2Fpasswd")
        assert res.status_code in (400, 404)

    async def test_put_replaces(self, http):
        client, _ = http
        orig = os.urandom(1024)
        signed = os.urandom(1200)
        upload = await client.post("/v1/assent/document", content=orig)
        doc_id = upload.json()["document_id"]

        replaced = await client.put(
            f"/v1/assent/document/{doc_id}",
            content=signed,
        )
        assert replaced.status_code == 204

        got = await client.get(f"/v1/assent/document/{doc_id}")
        assert got.content == signed

    async def test_put_unknown_id_404(self, http):
        client, _ = http
        res = await client.put(
            "/v1/assent/document/unknown123",
            content=b"whatever",
        )
        assert res.status_code == 404
