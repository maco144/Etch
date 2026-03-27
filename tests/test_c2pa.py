"""Tests for the Etch C2PA compatibility layer."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    from etch.c2pa import c2pa_router
    from etch.api import router as proof_router
    app = FastAPI()
    app.include_router(proof_router)
    app.include_router(c2pa_router)
    return app


async def _req(method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app()),
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, **kwargs)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _fake_chain_entry(leaf_index: int = 0) -> MagicMock:
    entry = MagicMock()
    entry.leaf_index = leaf_index
    entry.leaf_hash = "a" * 64
    entry.mmr_root = "b" * 64
    entry.payload_hash = "c" * 64
    entry.action_type = "c2pa_manifest"
    entry.created_at = 1710000000.0
    return entry


def _fake_proof() -> MagicMock:
    proof = MagicMock()
    proof.leaf_index = 0
    proof.leaf_hash = "a" * 64
    proof.mmr_root = "b" * 64
    proof.prev_root = "0" * 64
    proof.action_type = "c2pa_manifest"
    proof.payload_hash = "c" * 64
    proof.timestamp = 1710000000.0
    proof.to_dict.return_value = {
        "leaf_index": 0,
        "leaf_hash": "a" * 64,
        "mmr_root": "b" * 64,
        "prev_root": "0" * 64,
        "action_type": "c2pa_manifest",
        "payload_hash": "c" * 64,
        "timestamp": 1710000000.0,
    }
    return proof


SAMPLE_MANIFEST = {
    "content_hash": "a" * 64,
    "title": "Test Image",
    "format": "image/png",
    "claim": {
        "claim_generator": "TestSuite v1.0",
        "assertions": [
            {"label": "c2pa.created", "data": {"tool": "pytest"}},
            {"label": "c2pa.actions", "data": {"actions": ["generated"]}},
        ],
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRegisterManifest:
    @patch("etch.c2pa.get_session")
    @patch("etch.c2pa.log_event")
    @patch("etch.c2pa.get_chain")
    async def test_register_returns_receipt(self, mock_chain, mock_log, mock_session):
        mock_log.return_value = _fake_chain_entry()
        mock_chain.return_value.generate_proof.return_value = _fake_proof()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await _req("POST", "/v1/c2pa/manifest", json=SAMPLE_MANIFEST)

        assert resp.status_code == 200
        data = resp.json()
        assert data["claim_id"] == 0
        assert data["content_hash"] == "a" * 64
        assert data["claim_generator"] == "TestSuite v1.0"
        assert data["assertion_count"] == 2
        assert data["ingredient_count"] == 0
        assert "manifest_hash" in data
        assert "inclusion_proof" in data
        assert data["leaf_hash"] == "a" * 64
        assert data["mmr_root"] == "b" * 64

    @patch("etch.c2pa.get_session")
    @patch("etch.c2pa.log_event")
    @patch("etch.c2pa.get_chain")
    async def test_register_with_ingredients(self, mock_chain, mock_log, mock_session):
        mock_log.return_value = _fake_chain_entry()
        mock_chain.return_value.generate_proof.return_value = _fake_proof()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        manifest = {
            **SAMPLE_MANIFEST,
            "ingredients": [
                {
                    "title": "Original Photo",
                    "content_hash": "d" * 64,
                    "relationship": "parentOf",
                },
            ],
        }
        resp = await _req("POST", "/v1/c2pa/manifest", json=manifest)

        assert resp.status_code == 200
        assert resp.json()["ingredient_count"] == 1

    async def test_invalid_content_hash_returns_422(self):
        manifest = {**SAMPLE_MANIFEST, "content_hash": "tooshort"}
        resp = await _req("POST", "/v1/c2pa/manifest", json=manifest)
        assert resp.status_code == 422

    async def test_no_assertions_returns_422(self):
        manifest = {
            "content_hash": "a" * 64,
            "claim": {
                "claim_generator": "Test",
                "assertions": [],
            },
        }
        resp = await _req("POST", "/v1/c2pa/manifest", json=manifest)
        assert resp.status_code == 422

    @patch("etch.c2pa.get_session")
    @patch("etch.c2pa.log_event")
    @patch("etch.c2pa.get_chain")
    async def test_action_type_is_c2pa_manifest(self, mock_chain, mock_log, mock_session):
        mock_log.return_value = _fake_chain_entry()
        mock_chain.return_value.generate_proof.return_value = _fake_proof()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        await _req("POST", "/v1/c2pa/manifest", json=SAMPLE_MANIFEST)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert call_kwargs.kwargs.get("action_type") or call_kwargs[1].get("action_type") or \
            (len(call_kwargs.args) > 0 and call_kwargs.args[0]) == "c2pa_manifest"


class TestGetManifest:
    @patch("etch.c2pa.get_session")
    @patch("etch.c2pa.get_chain")
    async def test_retrieve_manifest(self, mock_chain, mock_session):
        mock_chain.return_value.generate_proof.return_value = _fake_proof()

        mock_record = MagicMock()
        mock_record.leaf_index = 0
        mock_record.content_hash = "a" * 64
        mock_record.leaf_hash = "a" * 64
        mock_record.mmr_root = "b" * 64
        mock_record.leaf_count = 1
        mock_record.action_type = "c2pa_manifest"
        mock_record.proof_json = json.dumps(SAMPLE_MANIFEST)
        mock_record.created_at_exact = 1710000000.0

        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_record
        session_mock.execute.return_value = result_mock

        mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await _req("GET", "/v1/c2pa/manifest/0")

        assert resp.status_code == 200
        data = resp.json()
        assert data["claim_id"] == 0
        assert data["manifest"]["content_hash"] == "a" * 64
        assert "inclusion_proof" in data
        assert "verification_steps" in data

    @patch("etch.c2pa.get_session")
    async def test_not_found(self, mock_session):
        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session_mock.execute.return_value = result_mock

        mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await _req("GET", "/v1/c2pa/manifest/999")
        assert resp.status_code == 404


class TestVerifyManifest:
    @patch("etch.c2pa.get_session")
    async def test_verify_chain_integrity(self, mock_session):
        # Create a record with correct chain integrity
        prev_root = "0" * 64
        payload_hash = "c" * 64
        ts = 1710000000.0
        expected_leaf = _sha256(f"{prev_root}:c2pa_manifest:{payload_hash}:{ts}")

        mock_record = MagicMock()
        mock_record.leaf_index = 0
        mock_record.content_hash = "a" * 64
        mock_record.leaf_hash = expected_leaf
        mock_record.mmr_root = "b" * 64
        mock_record.payload_hash = payload_hash
        mock_record.action_type = "c2pa_manifest"
        mock_record.proof_json = json.dumps(SAMPLE_MANIFEST)
        mock_record.created_at_exact = ts

        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [mock_record]
        session_mock.execute.return_value = result_mock

        mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await _req("POST", "/v1/c2pa/verify", json={
            "claim_id": 0,
            "content_hash": "a" * 64,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["chain_integrity"] is True
        assert data["content_hash_match"] is True
        assert data["verified"] is True

    @patch("etch.c2pa.get_session")
    async def test_verify_wrong_content_hash(self, mock_session):
        prev_root = "0" * 64
        payload_hash = "c" * 64
        ts = 1710000000.0
        expected_leaf = _sha256(f"{prev_root}:c2pa_manifest:{payload_hash}:{ts}")

        mock_record = MagicMock()
        mock_record.leaf_index = 0
        mock_record.content_hash = "a" * 64
        mock_record.leaf_hash = expected_leaf
        mock_record.mmr_root = "b" * 64
        mock_record.payload_hash = payload_hash
        mock_record.action_type = "c2pa_manifest"
        mock_record.proof_json = json.dumps(SAMPLE_MANIFEST)
        mock_record.created_at_exact = ts

        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [mock_record]
        session_mock.execute.return_value = result_mock

        mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await _req("POST", "/v1/c2pa/verify", json={
            "claim_id": 0,
            "content_hash": "f" * 64,  # wrong hash
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["content_hash_match"] is False
        assert data["verified"] is False

    @patch("etch.c2pa.get_session")
    async def test_verify_not_found(self, mock_session):
        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        session_mock.execute.return_value = result_mock

        mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await _req("POST", "/v1/c2pa/verify", json={"claim_id": 999})
        assert resp.status_code == 404


class TestBridge:
    @patch("etch.c2pa.get_session")
    @patch("etch.c2pa.log_event")
    @patch("etch.c2pa.get_chain")
    async def test_bridge_existing_proof(self, mock_chain, mock_log, mock_session):
        mock_log.return_value = _fake_chain_entry(leaf_index=1)
        mock_chain.return_value.generate_proof.return_value = _fake_proof()

        # Original proof record
        original = MagicMock()
        original.leaf_index = 0
        original.content_hash = "d" * 64
        original.leaf_hash = "e" * 64
        original.mmr_root = "f" * 64
        original.label = "Original Article"
        original.created_at_exact = 1710000000.0

        # Session mock — first call returns original, second call persists
        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = original
        session_mock.execute.return_value = result_mock

        mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await _req("POST", "/v1/c2pa/bridge", json={
            "proof_id": 0,
            "claim_generator": "Etch Bridge",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["bridged"] is True
        assert data["original_proof_id"] == 0
        assert data["content_hash"] == "d" * 64
        assert "c2pa_claim_id" in data

    @patch("etch.c2pa.get_session")
    async def test_bridge_not_found(self, mock_session):
        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session_mock.execute.return_value = result_mock

        mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await _req("POST", "/v1/c2pa/bridge", json={
            "proof_id": 999,
            "claim_generator": "Test",
        })
        assert resp.status_code == 404
