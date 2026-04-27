"""Tests for /v1/proof/embed-audio and /v1/proof/extract-audio."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from etch.glyph import encode_shortcode
from etch.watermark.io import write_wav


# ---------------------------------------------------------------------------
# App + helpers
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    from etch.watermark_api import watermark_router
    app = FastAPI()
    app.include_router(watermark_router)
    return app


async def _post_audio(path: str, blob: bytes, filename: str = "track.wav") -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app()),
        base_url="http://testserver",
    ) as client:
        files = {"file": (filename, blob, "audio/wav")}
        return await client.post(path, files=files)


def _music_like(seconds: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * sr)
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0)
    freqs[0] = freqs[1]
    spec = spec / np.sqrt(freqs)
    out = np.fft.irfft(spec, n=n)
    out /= np.max(np.abs(out)) + 1e-12
    audio = (out * 0.3).astype(np.float64)
    t = np.arange(n) / sr
    for f, amp in [(220.0, 0.05), (440.0, 0.04), (880.0, 0.03)]:
        audio = audio + amp * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    audio = audio / (np.max(np.abs(audio)) + 1e-12) * 0.6
    return audio


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


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------

class TestEmbedAudio:
    @pytest.mark.asyncio
    async def test_embed_returns_wav_with_headers(self):
        rng = np.random.default_rng(0)
        sr = 22050
        wav = write_wav(_music_like(150.0, sr, rng), sr)
        ch = "f" * 64
        rec = _fake_record(123, ch)
        expected_sc = encode_shortcode(123, ch)

        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=rec):
            resp = await _post_audio("/v1/proof/123/embed-audio", wav)

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        assert resp.headers["X-Etch-Shortcode"] == expected_sc
        assert resp.headers["X-Etch-Proof-Id"] == "123"
        assert resp.content[:4] == b"RIFF"
        # Filename is in the Content-Disposition.
        assert f"etched-{expected_sc}.wav" in resp.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_embed_returns_404_for_missing_proof(self):
        rng = np.random.default_rng(1)
        sr = 22050
        wav = write_wav(_music_like(150.0, sr, rng), sr)

        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=None):
            resp = await _post_audio("/v1/proof/999/embed-audio", wav)

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_embed_rejects_short_audio(self):
        rng = np.random.default_rng(2)
        sr = 22050
        wav = write_wav(_music_like(10.0, sr, rng), sr)  # way under 60s
        rec = _fake_record(1, "f" * 64)

        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=rec):
            resp = await _post_audio("/v1/proof/1/embed-audio", wav)

        assert resp.status_code == 422
        assert "60s" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_embed_rejects_garbage_audio(self):
        rec = _fake_record(1, "f" * 64)
        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=rec):
            resp = await _post_audio("/v1/proof/1/embed-audio", b"this is not audio")
        # ffmpeg will fail to decode → 415
        assert resp.status_code == 415


# ---------------------------------------------------------------------------
# Extract — full round-trip via the embed endpoint
# ---------------------------------------------------------------------------

class TestExtractAudio:
    @pytest.mark.asyncio
    async def test_round_trip_embed_then_extract(self):
        rng = np.random.default_rng(42)
        sr = 22050
        wav = write_wav(_music_like(150.0, sr, rng), sr)
        ch = "f" * 64
        rec = _fake_record(7, ch, label="My song", owner="alice")
        expected_sc = encode_shortcode(7, ch)

        # 1. Embed
        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=rec):
            embed_resp = await _post_audio("/v1/proof/7/embed-audio", wav)
        assert embed_resp.status_code == 200
        watermarked_wav = embed_resp.content

        # 2. Extract — same record returned for the lookup of recovered proof_id.
        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=rec):
            ext_resp = await _post_audio("/v1/proof/extract-audio", watermarked_wav)

        assert ext_resp.status_code == 200
        body = ext_resp.json()
        assert body["found"] is True
        assert body["resolved"]["proof_id"] == 7
        assert body["resolved"]["shortcode"] == expected_sc
        assert body["resolved"]["content_hash"] == ch
        assert body["resolved"]["label"] == "My song"
        assert body["resolved"]["owner"] == "alice"
        assert body["resolved"]["magic_url"].endswith(f"/g/{expected_sc}")

    @pytest.mark.asyncio
    async def test_extract_unwatermarked_returns_not_found(self):
        rng = np.random.default_rng(99)
        sr = 22050
        wav = write_wav(_music_like(150.0, sr, rng), sr)

        # No DB lookup should be needed — extract returns early when no
        # payload is decoded. Patch defensively anyway.
        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=None):
            resp = await _post_audio("/v1/proof/extract-audio", wav)

        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is False
        assert body["resolved"] is None

    @pytest.mark.asyncio
    async def test_extract_handles_unknown_proof_id_gracefully(self):
        # Embed to proof_id=7, but on extract pretend the DB has no such row.
        rng = np.random.default_rng(123)
        sr = 22050
        wav = write_wav(_music_like(150.0, sr, rng), sr)
        rec = _fake_record(7, "f" * 64)

        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=rec):
            embed_resp = await _post_audio("/v1/proof/7/embed-audio", wav)
        watermarked = embed_resp.content

        with patch("etch.watermark_api._load_record", new_callable=AsyncMock, return_value=None):
            ext_resp = await _post_audio("/v1/proof/extract-audio", watermarked)

        body = ext_resp.json()
        assert body["found"] is False
        assert "unknown proof_id" in body["error"]

    @pytest.mark.asyncio
    async def test_extract_rejects_short_audio(self):
        rng = np.random.default_rng(0)
        sr = 22050
        wav = write_wav(_music_like(5.0, sr, rng), sr)
        resp = await _post_audio("/v1/proof/extract-audio", wav)
        assert resp.status_code == 422
