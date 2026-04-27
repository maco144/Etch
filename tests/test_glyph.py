"""Tests for the Etch glyph module + glyph API."""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from etch.glyph import (
    decode_shortcode,
    encode_shortcode,
    format_shortcode,
    render_bar_sigil,
    verify_shortcode,
)


# ---------------------------------------------------------------------------
# Glyph encoding (no I/O)
# ---------------------------------------------------------------------------

class TestShortcode:
    def test_round_trip(self):
        for pid in [0, 1, 42, 0x12345678, 0xFFFFFFFF]:
            ch = hashlib.sha256(str(pid).encode()).hexdigest()
            sc = encode_shortcode(pid, ch)
            assert len(sc) == 8
            decoded_pid, _ = decode_shortcode(sc)
            assert decoded_pid == pid
            assert verify_shortcode(sc, ch)

    def test_checksum_detects_wrong_hash(self):
        pid = 100
        right = "a" * 64
        wrong = "b" * 64
        sc = encode_shortcode(pid, right)
        assert verify_shortcode(sc, right)
        assert not verify_shortcode(sc, wrong)

    def test_decode_accepts_dashes_and_lowercase(self):
        sc = encode_shortcode(42, "a" * 64)
        pretty = format_shortcode(sc).lower()  # e.g. "abcd-efgh"
        decoded_pid, _ = decode_shortcode(pretty)
        assert decoded_pid == 42

    def test_decode_substitutes_ambiguous_chars(self):
        # Encode something deterministic, then in the shortcode replace any
        # 1 with I, 0 with O — decoder should still map them back.
        sc = encode_shortcode(1, "f" * 64)
        substituted = sc.replace("1", "I").replace("0", "O")
        decoded_pid, _ = decode_shortcode(substituted)
        assert decoded_pid == 1

    def test_decode_rejects_garbage(self):
        with pytest.raises(ValueError):
            decode_shortcode("ABC")  # too short
        with pytest.raises(ValueError):
            decode_shortcode("@@@@@@@@")  # invalid chars

    def test_pack_oversized_proof_id(self):
        with pytest.raises(ValueError):
            encode_shortcode(1 << 32, "a" * 64)

    def test_render_bar_sigil_returns_png(self):
        sc = encode_shortcode(42, "a" * 64)
        png = render_bar_sigil(sc)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        # Larger size variant
        big = render_bar_sigil(sc, width=1200, height=300)
        assert big.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(big) > len(png)


# ---------------------------------------------------------------------------
# Glyph API — resolver endpoints
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    from etch.glyph_api import glyph_router
    app = FastAPI()
    app.include_router(glyph_router)
    return app


async def _req(method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app()),
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, **kwargs)


def _fake_record(proof_id: int, content_hash: str, label: str = "Track A",
                 owner: str = "alice") -> MagicMock:
    rec = MagicMock()
    rec.leaf_index = proof_id
    rec.content_hash = content_hash
    rec.label = label
    rec.owner = owner
    rec.created_at.timestamp.return_value = 1700000000.0
    rec.leaf_hash = "a" * 64
    rec.mmr_root = "b" * 64
    rec.leaf_count = proof_id + 1
    return rec


class TestResolverEndpoints:
    @pytest.mark.asyncio
    async def test_resolver_json_returns_record(self):
        ch = "f" * 64
        rec = _fake_record(42, ch)
        sc = encode_shortcode(42, ch)

        with patch("etch.glyph_api._load_record", new_callable=AsyncMock, return_value=rec):
            resp = await _req("GET", f"/g/{sc}.json")

        assert resp.status_code == 200
        body = resp.json()
        assert body["proof_id"] == 42
        assert body["content_hash"] == ch
        assert body["registered_at"] == 1700000000.0

    @pytest.mark.asyncio
    async def test_resolver_html_renders_record(self):
        ch = "f" * 64
        rec = _fake_record(42, ch, label="Hello", owner="bob")
        sc = encode_shortcode(42, ch)

        with patch("etch.glyph_api._load_record", new_callable=AsyncMock, return_value=rec):
            resp = await _req("GET", f"/g/{sc}")

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        assert ch in body
        assert "Hello" in body
        assert "bob" in body
        assert format_shortcode(sc) in body

    @pytest.mark.asyncio
    async def test_resolver_returns_404_for_missing_record(self):
        sc = encode_shortcode(99, "f" * 64)
        with patch("etch.glyph_api._load_record", new_callable=AsyncMock, return_value=None):
            resp = await _req("GET", f"/g/{sc}.json")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_resolver_rejects_checksum_mismatch(self):
        # Encode a shortcode for hash X, then mock the DB to return a record
        # with hash Y. Resolver must reject — the shortcode is not for this record.
        sc = encode_shortcode(42, "a" * 64)
        rec = _fake_record(42, "b" * 64)  # different hash
        with patch("etch.glyph_api._load_record", new_callable=AsyncMock, return_value=rec):
            resp = await _req("GET", f"/g/{sc}.json")
        assert resp.status_code == 404
        assert "checksum" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_resolver_rejects_invalid_shortcode(self):
        resp = await _req("GET", "/g/SHORT.json")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_sigil_png_endpoint(self):
        ch = "f" * 64
        rec = _fake_record(42, ch)
        sc = encode_shortcode(42, ch)

        with patch("etch.glyph_api._load_record", new_callable=AsyncMock, return_value=rec):
            resp = await _req("GET", f"/g/{sc}.png")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.asyncio
    async def test_create_glyph_returns_bundle(self):
        ch = "f" * 64
        rec = _fake_record(42, ch)
        expected_sc = encode_shortcode(42, ch)

        with patch("etch.glyph_api._load_record", new_callable=AsyncMock, return_value=rec):
            resp = await _req("POST", "/v1/proof/42/glyph")

        assert resp.status_code == 200
        body = resp.json()
        assert body["proof_id"] == 42
        assert body["shortcode"] == expected_sc
        assert body["pretty"] == format_shortcode(expected_sc)
        assert body["magic_url"].endswith(f"/g/{expected_sc}")
        assert body["sigil_png_url"].endswith(f"/g/{expected_sc}.png")

    @pytest.mark.asyncio
    async def test_create_glyph_404_for_missing(self):
        with patch("etch.glyph_api._load_record", new_callable=AsyncMock, return_value=None):
            resp = await _req("POST", "/v1/proof/999/glyph")
        assert resp.status_code == 404
