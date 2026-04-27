"""Tests for the etchmark audio watermark pipeline."""
from __future__ import annotations

import numpy as np
import pytest

from etch.watermark.payload import (
    PAYLOAD_BITS,
    pack_payload,
    payload_bits_repeated,
    unpack_payload,
    crc12,
    PayloadError,
)
from etch.watermark.pipeline import embed, extract
from etch.watermark.sync import find_alignment, majority_decode


# ---------------------------------------------------------------------------
# Synthetic audio generators — stand in for real music in unit tests.
# ---------------------------------------------------------------------------

def _pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f noise — closer to music spectrum than white noise."""
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0)
    freqs[0] = freqs[1]  # avoid div-by-zero at DC
    spec = spec / np.sqrt(freqs)
    out = np.fft.irfft(spec, n=n)
    out /= np.max(np.abs(out)) + 1e-12
    return (out * 0.3).astype(np.float64)


def _music_like(seconds: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Pink noise + a few stationary tones — broader spectrum stand-in for music."""
    n = int(seconds * sr)
    audio = _pink_noise(n, rng)
    t = np.arange(n) / sr
    for f, amp in [(220.0, 0.05), (440.0, 0.04), (880.0, 0.03), (1760.0, 0.02)]:
        audio = audio + amp * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    audio = audio / (np.max(np.abs(audio)) + 1e-12) * 0.6
    return audio


# ---------------------------------------------------------------------------
# Payload-level tests
# ---------------------------------------------------------------------------

class TestPayload:
    def test_pack_unpack_round_trip(self):
        for sc_int in [0, 1, 0xDEADBEEF, 0xFFFFFFFF, 12345678]:
            bits = pack_payload(sc_int)
            assert bits.shape == (PAYLOAD_BITS,)
            v, s = unpack_payload(bits)
            assert s == sc_int
            assert v == 1

    def test_unpack_rejects_bad_sync(self):
        bits = pack_payload(42)
        bits[0] ^= 1  # flip first sync bit
        with pytest.raises(PayloadError, match="sync"):
            unpack_payload(bits)

    def test_unpack_rejects_bad_crc(self):
        bits = pack_payload(42)
        bits[-1] ^= 1  # flip last CRC bit
        with pytest.raises(PayloadError, match="CRC"):
            unpack_payload(bits)

    def test_pack_rejects_oversized_shortcode(self):
        with pytest.raises(ValueError):
            pack_payload(1 << 32)

    def test_crc12_changes_with_input(self):
        a = crc12(0xABCDE, 36)
        b = crc12(0xABCDF, 36)
        assert a != b

    def test_payload_bits_repeated_tiles_correctly(self):
        bits = pack_payload(0xCAFEBABE)
        tiled = payload_bits_repeated(bits, 200)
        assert tiled.size == 200
        assert np.array_equal(tiled[:PAYLOAD_BITS], bits)
        assert np.array_equal(tiled[PAYLOAD_BITS : 2 * PAYLOAD_BITS], bits)


# ---------------------------------------------------------------------------
# Sync-level tests
# ---------------------------------------------------------------------------

class TestSync:
    def test_alignment_recovered_under_arbitrary_trim(self):
        # Build a long tiled stream and trim off the start — simulates a
        # decoder seeing audio with `trim` chunks lopped off the beginning.
        sc_int = 0xCAFEBABE
        bits = pack_payload(sc_int)
        full = payload_bits_repeated(bits, 5 * PAYLOAD_BITS)  # 5 full reps
        soft_full = np.where(full == 1, 1.0, -1.0)

        for trim in [0, 7, 17, 31, 55]:
            soft = soft_full[trim:]
            offset, score = find_alignment(soft)
            assert offset == trim, f"trim={trim}: got {offset}, want {trim}"
            assert score > 0

            hard = majority_decode(soft, offset)
            v, s = unpack_payload(hard)
            assert s == sc_int


# ---------------------------------------------------------------------------
# Full pipeline round-trip on synthesized audio
# ---------------------------------------------------------------------------

class TestPipelineRoundTrip:
    def test_round_trip_music_like(self):
        rng = np.random.default_rng(123)
        sr = 22050
        # 150s → ~2.7 repetitions of the 56-chunk payload, enough for
        # majority voting to drive bit-error rate well below 1/56.
        audio = _music_like(150.0, sr, rng)
        sc_int = 0x12345678

        watermarked = embed(audio, sr, sc_int)
        assert watermarked.shape == audio.shape

        # Watermark must be inaudible-ish: peak deviation tiny.
        delta = watermarked - audio
        peak_delta_db = 20 * np.log10(np.max(np.abs(delta)) / (np.max(np.abs(audio)) + 1e-12))
        assert peak_delta_db < -40, f"watermark too loud: {peak_delta_db:.1f} dB"

        result = extract(watermarked, sr)
        assert result.found, f"extraction failed: {result.error}"
        assert result.shortcode_int == sc_int
        assert result.version == 1

    def test_extract_returns_not_found_on_clean_audio(self):
        rng = np.random.default_rng(7)
        sr = 22050
        audio = _music_like(150.0, sr, rng)
        result = extract(audio, sr)
        assert not result.found
        assert result.error is not None

    def test_round_trip_survives_volume_normalization(self):
        rng = np.random.default_rng(456)
        sr = 22050
        audio = _music_like(150.0, sr, rng)
        sc_int = 0xABCDEF01

        watermarked = embed(audio, sr, sc_int)
        # Simulate platform LUFS normalization: arbitrary gain change.
        normalized = watermarked * 0.4

        result = extract(normalized, sr)
        assert result.found, f"extract failed under gain change: {result.error}"
        assert result.shortcode_int == sc_int

    def test_round_trip_survives_trim(self):
        rng = np.random.default_rng(789)
        sr = 22050
        audio = _music_like(180.0, sr, rng)
        sc_int = 0x55AA55AA

        watermarked = embed(audio, sr, sc_int)
        # Trim 17 seconds off the start (mid-payload trim — sync must recover).
        trimmed = watermarked[17 * sr :]

        result = extract(trimmed, sr)
        assert result.found, f"extract failed under trim: {result.error}"
        assert result.shortcode_int == sc_int

    def test_audio_too_short_raises(self):
        sr = 22050
        audio = np.zeros(10 * sr, dtype=np.float64)  # 10 chunks, need 56
        with pytest.raises(ValueError, match="too short"):
            embed(audio, sr, 0xDEADBEEF)
