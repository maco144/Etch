"""Tests for the Etch proof API."""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Test app — just the proof router, no DB needed for chain-only tests
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    from etch.api import router
    app = FastAPI()
    app.include_router(router)
    return app


async def _req(method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app()),
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, **kwargs)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _fake_chain_entry(leaf_index: int = 42, content_hash: str = None) -> MagicMock:
    entry = MagicMock()
    entry.leaf_index = leaf_index
    entry.leaf_hash = "a" * 64
    entry.mmr_root = "b" * 64
    entry.leaf_count = leaf_index + 1
    entry.created_at = 1700000000.0
    entry.action_type = "content_proof"
    entry.agent_id = content_hash or _sha256("test content")
    entry.payload_hash = "c" * 64
    return entry


# ---------------------------------------------------------------------------
# POST /v1/proof
# ---------------------------------------------------------------------------

class TestRegisterProof:

    @pytest.mark.asyncio
    async def test_content_field_hashes_and_returns_receipt(self):
        content = "My original article"
        expected_hash = _sha256(content)
        fake_entry = _fake_chain_entry(leaf_index=10, content_hash=expected_hash)

        with patch("etch.api.log_event", return_value=fake_entry), \
             patch("etch.api._persist_to_db", new_callable=AsyncMock):
            resp = await _req("POST", "/v1/proof", json={"content": content})

        assert resp.status_code == 200
        body = resp.json()
        assert body["content_hash"] == expected_hash
        assert body["proof_id"] == 10
        assert len(body["leaf_hash"]) == 64
        assert len(body["mmr_root"]) == 64

    @pytest.mark.asyncio
    async def test_content_hash_field_accepted(self):
        pre_hash = "d" * 64
        fake_entry = _fake_chain_entry(leaf_index=5, content_hash=pre_hash)

        with patch("etch.api.log_event", return_value=fake_entry), \
             patch("etch.api._persist_to_db", new_callable=AsyncMock):
            resp = await _req("POST", "/v1/proof", json={"content_hash": pre_hash})

        assert resp.status_code == 200
        assert resp.json()["content_hash"] == pre_hash

    @pytest.mark.asyncio
    async def test_neither_field_returns_422(self):
        resp = await _req("POST", "/v1/proof", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_hash_length_returns_422(self):
        resp = await _req("POST", "/v1/proof", json={"content_hash": "tooshort"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_log_event_called_with_content_proof_action(self):
        content = "Track this"
        fake_entry = _fake_chain_entry()

        with patch("etch.api.log_event", return_value=fake_entry) as mock_log, \
             patch("etch.api._persist_to_db", new_callable=AsyncMock):
            await _req("POST", "/v1/proof", json={"content": content})

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert call_kwargs.kwargs["action_type"] == "content_proof"
        assert call_kwargs.kwargs["specialist"] == "etch"
        assert call_kwargs.kwargs["agent_id"] == _sha256(content)

    @pytest.mark.asyncio
    async def test_label_and_owner_in_response(self):
        fake_entry = _fake_chain_entry()

        with patch("etch.api.log_event", return_value=fake_entry), \
             patch("etch.api._persist_to_db", new_callable=AsyncMock):
            resp = await _req("POST", "/v1/proof", json={
                "content": "hello",
                "label": "Test label",
                "owner": "owner-abc",
            })

        body = resp.json()
        assert body["label"] == "Test label"
        assert body["owner"] == "owner-abc"
