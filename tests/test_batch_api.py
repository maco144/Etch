"""Tests for the Etch batch proof API."""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Test app
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


_call_counter = 0


def _fake_chain_entry_factory():
    """Return a function that creates unique chain entries on each call."""
    counter = 0

    def _make(leaf_index=None, **kwargs):
        nonlocal counter
        if leaf_index is None:
            leaf_index = counter
        counter += 1
        entry = MagicMock()
        entry.leaf_index = leaf_index
        entry.leaf_hash = hashlib.sha256(f"leaf_{leaf_index}".encode()).hexdigest()
        entry.mmr_root = hashlib.sha256(f"root_{leaf_index}".encode()).hexdigest()
        entry.leaf_count = leaf_index + 1
        entry.created_at = 1700000000.0 + leaf_index
        entry.action_type = "content_proof"
        entry.payload_hash = hashlib.sha256(f"payload_{leaf_index}".encode()).hexdigest()
        return entry

    return _make


def _mock_log_event_factory():
    """Return a side_effect function that creates incrementing chain entries."""
    factory = _fake_chain_entry_factory()
    return lambda **kwargs: factory()


# ---------------------------------------------------------------------------
# POST /v1/proof/batch
# ---------------------------------------------------------------------------

class TestBatchRegister:

    @pytest.mark.asyncio
    async def test_register_three_items(self):
        """Register 3 items in a batch and get 3 receipts."""
        side_effect = _mock_log_event_factory()

        with patch("etch.api.log_event", side_effect=side_effect), \
             patch("etch.api.get_session") as mock_session:
            # Mock the DB session context manager
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = mock_ctx
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = await _req("POST", "/v1/proof/batch", json={
                "items": [
                    {"content": "Article one"},
                    {"content": "Article two"},
                    {"content": "Article three"},
                ],
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        assert len(body["receipts"]) == 3

        # Each receipt should have a valid proof_id and content_hash
        for i, result in enumerate(body["receipts"]):
            assert result["index"] == i
            assert result["receipt"] is not None
            assert result["error"] is None
            assert len(result["receipt"]["content_hash"]) == 64
            assert len(result["receipt"]["leaf_hash"]) == 64

    @pytest.mark.asyncio
    async def test_mix_of_content_and_content_hash(self):
        """Batch with both content and content_hash items."""
        side_effect = _mock_log_event_factory()
        pre_hash = "d" * 64

        with patch("etch.api.log_event", side_effect=side_effect), \
             patch("etch.api.get_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = mock_ctx
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = await _req("POST", "/v1/proof/batch", json={
                "items": [
                    {"content": "Raw content here"},
                    {"content_hash": pre_hash},
                    {"content": "More content", "label": "My Label", "owner": "alice"},
                ],
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3

        # First item: hashed from content
        assert body["receipts"][0]["receipt"]["content_hash"] == _sha256("Raw content here")
        # Second item: pre-computed hash
        assert body["receipts"][1]["receipt"]["content_hash"] == pre_hash
        # Third item: has label and owner
        assert body["receipts"][2]["receipt"]["label"] == "My Label"
        assert body["receipts"][2]["receipt"]["owner"] == "alice"

    @pytest.mark.asyncio
    async def test_invalid_items_return_errors(self):
        """Invalid items in the batch get error messages, valid ones succeed."""
        side_effect = _mock_log_event_factory()

        with patch("etch.api.log_event", side_effect=side_effect), \
             patch("etch.api.get_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = mock_ctx
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = await _req("POST", "/v1/proof/batch", json={
                "items": [
                    {"content": "Valid item"},
                    {},  # Missing both content and content_hash
                    {"content_hash": "tooshort"},  # Invalid hash length
                    {"content": "Another valid item"},
                ],
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2  # Only 2 valid items

        # First item: success
        assert body["receipts"][0]["receipt"] is not None
        assert body["receipts"][0]["error"] is None

        # Second item: error (no content or content_hash)
        assert body["receipts"][1]["receipt"] is None
        assert "content" in body["receipts"][1]["error"].lower()

        # Third item: error (bad hash length)
        assert body["receipts"][2]["receipt"] is None
        assert "64" in body["receipts"][2]["error"]

        # Fourth item: success
        assert body["receipts"][3]["receipt"] is not None
        assert body["receipts"][3]["error"] is None

    @pytest.mark.asyncio
    async def test_empty_batch_returns_422(self):
        """Empty items list should fail validation."""
        resp = await _req("POST", "/v1/proof/batch", json={"items": []})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_batch_over_1000_returns_422(self):
        """More than 1000 items should fail validation."""
        items = [{"content": f"item_{i}"} for i in range(1001)]
        resp = await _req("POST", "/v1/proof/batch", json={"items": items})
        assert resp.status_code == 422
