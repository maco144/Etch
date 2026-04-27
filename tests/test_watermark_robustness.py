"""
Robustness tests for the etchmark watermark.

Embeds a watermark, then runs the watermarked audio through real ffmpeg
re-encode pipelines (MP3, AAC, Opus at multiple bitrates) and verifies that
extraction still succeeds. These exercise the spread-spectrum layer against
actual lossy compression — they're the closest thing to a YouTube/X/BitChute
upload pipeline we can run in CI.

Marked `slow`: each ffmpeg round-trip is ~1s. Skipped if ffmpeg is not on
PATH.
"""
from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from etch.watermark.io import read_audio, write_wav
from etch.watermark.pipeline import embed, extract


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"),
]


# ---------------------------------------------------------------------------
# Synthetic audio — same generator as the unit tests, deterministic.
# ---------------------------------------------------------------------------

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
    for f, amp in [(220.0, 0.05), (440.0, 0.04), (880.0, 0.03), (1760.0, 0.02)]:
        audio = audio + amp * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    audio = audio / (np.max(np.abs(audio)) + 1e-12) * 0.6
    return audio


def _ffmpeg_transcode(wav_blob: bytes, *codec_args: str) -> bytes:
    """Run ffmpeg over WAV input → encoded blob in stdout."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-vn", *codec_args, "pipe:1"],
        input=wav_blob, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg failed: {msg.strip()[-300:]}")
    return proc.stdout


# ---------------------------------------------------------------------------
# Fixture — one watermarked WAV reused across all codec/bitrate combinations.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def watermarked_wav() -> tuple[bytes, int, int]:
    """Return (wav_blob, sr, shortcode_int) of a 180s watermarked test track."""
    rng = np.random.default_rng(2026)
    sr = 22050
    audio = _music_like(180.0, sr, rng)
    sc_int = 0x12ABCDEF
    watermarked = embed(audio, sr, sc_int)
    return write_wav(watermarked, sr), sr, sc_int


# ---------------------------------------------------------------------------
# Codec matrix — the bitrates here are deliberately on the LOW end of what
# real platforms use, so passing here means we'd survive higher-quality
# pipelines too.
# ---------------------------------------------------------------------------

CODEC_MATRIX = [
    # (label, ffmpeg_args, output_filename_hint)
    # Bitrates here are deliberately on the low end of what real platforms use;
    # passing here implies higher-quality pipelines also pass. Opus at 96k is
    # below the spread layer's reliable threshold (Layer 1 echo-hiding, future
    # work, will raise that floor).
    ("mp3-128k",  ("-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"),     "out.mp3"),
    ("mp3-192k",  ("-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3"),     "out.mp3"),
    ("aac-128k",  ("-c:a", "aac",        "-b:a", "128k", "-f", "adts"),    "out.aac"),
    ("opus-128k", ("-c:a", "libopus",    "-b:a", "128k", "-f", "ogg"),     "out.ogg"),
]


@pytest.mark.parametrize("label,codec_args,hint", CODEC_MATRIX, ids=[c[0] for c in CODEC_MATRIX])
def test_extract_survives_lossy_reencode(watermarked_wav, label, codec_args, hint):
    wav, sr, expected_sc_int = watermarked_wav

    encoded = _ffmpeg_transcode(wav, *codec_args)
    assert len(encoded) > 0

    audio, decoded_sr = read_audio(encoded, filename_hint=hint)
    # Codecs may resample — extract works at whatever sr it's given.
    result = extract(audio, decoded_sr)

    assert result.found, (
        f"{label}: watermark not recovered after {label} re-encode "
        f"(error={result.error}, sync_score={result.sync_score})"
    )
    assert result.shortcode_int == expected_sc_int, (
        f"{label}: recovered wrong shortcode {result.shortcode_int:#x} "
        f"(expected {expected_sc_int:#x})"
    )


def test_extract_survives_lufs_normalization(watermarked_wav):
    """Simulate a LUFS normalizer — uniform gain change."""
    wav, sr, expected_sc_int = watermarked_wav
    encoded = _ffmpeg_transcode(
        wav,
        "-af", "volume=0.4",  # -8 dB-ish
        "-c:a", "pcm_s16le", "-f", "wav",
    )
    audio, decoded_sr = read_audio(encoded, filename_hint="out.wav")
    result = extract(audio, decoded_sr)
    assert result.found, f"LUFS-norm failed: {result.error}"
    assert result.shortcode_int == expected_sc_int


def test_extract_survives_mp3_then_aac(watermarked_wav):
    """
    Two-codec chain: MP3 first (typical "intermediate" upload), then AAC
    (typical platform re-encode). The watermark has to survive both lossy
    passes — the most realistic worst-case pipeline.
    """
    wav, sr, expected_sc_int = watermarked_wav
    mp3 = _ffmpeg_transcode(wav, "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3")
    aac = _ffmpeg_transcode(mp3, "-c:a", "aac", "-b:a", "128k", "-f", "adts")
    audio, decoded_sr = read_audio(aac, filename_hint="out.aac")
    result = extract(audio, decoded_sr)
    assert result.found, f"MP3→AAC chain failed: {result.error}"
    assert result.shortcode_int == expected_sc_int
