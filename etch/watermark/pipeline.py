"""
High-level watermark pipeline — embed() and extract() over full audio.

Both functions operate on mono float32 audio (range roughly [-1, 1]). Stereo
callers should downmix or watermark each channel independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .payload import (
    PAYLOAD_BITS,
    pack_payload,
    payload_bits_repeated,
    unpack_payload,
    PayloadError,
)
from .spread import (
    DEFAULT_ALPHA,
    DEFAULT_BAND_HZ,
    DEFAULT_BINS_PER_CHUNK,
    DEFAULT_SEED,
    detect_chunk,
    embed_chunk,
)
from .sync import majority_decode, rank_alignments

DEFAULT_CHUNK_SECONDS = 1.0


@dataclass
class ExtractResult:
    """Outcome of a watermark extraction attempt."""
    found: bool
    shortcode_int: Optional[int]
    version: Optional[int]
    n_chunks: int
    n_repetitions: int
    sync_offset: Optional[int]
    sync_score: Optional[float]
    error: Optional[str] = None


def _split_chunks(audio: np.ndarray, chunk_size: int) -> tuple[np.ndarray, int]:
    """
    Split audio into chunks. Returns (chunked, n_chunks). Trailing samples
    that don't fill a chunk are dropped (they can't carry a bit).
    """
    n = (audio.size // chunk_size) * chunk_size
    return audio[:n].reshape(-1, chunk_size), n // chunk_size


def embed(
    audio: np.ndarray,
    sr: int,
    shortcode_int: int,
    *,
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    seed: str = DEFAULT_SEED,
    band: tuple[float, float] = DEFAULT_BAND_HZ,
    n_bins: int = DEFAULT_BINS_PER_CHUNK,
    alpha: float = DEFAULT_ALPHA,
) -> np.ndarray:
    """
    Embed a watermark carrying `shortcode_int` into `audio`.

    Args:
        audio: 1-D float array, mono.
        sr: sample rate (Hz).
        shortcode_int: 32-bit integer derived from the Etch shortcode.

    Returns:
        Watermarked audio of the same shape as `audio`. The trailing
        sub-chunk tail (if any) is passed through untouched.
    """
    if audio.ndim != 1:
        raise ValueError("embed expects 1-D mono audio")

    chunk_size = int(round(chunk_seconds * sr))
    if chunk_size <= 0:
        raise ValueError("chunk_seconds * sr must be positive")

    chunked, n_chunks = _split_chunks(audio, chunk_size)
    if n_chunks < PAYLOAD_BITS:
        raise ValueError(
            f"audio too short: {n_chunks} chunks at {chunk_seconds}s each, "
            f"need at least {PAYLOAD_BITS}"
        )

    bits = pack_payload(shortcode_int)
    chunk_bits = payload_bits_repeated(bits, n_chunks)

    out_chunks = np.empty_like(chunked)
    for i in range(n_chunks):
        out_chunks[i] = embed_chunk(
            chunked[i], int(chunk_bits[i]), sr,
            seed=seed, band=band, n_bins=n_bins, alpha=alpha,
        )

    # Stitch chunks back, append untouched tail.
    out = np.empty_like(audio)
    out[: n_chunks * chunk_size] = out_chunks.reshape(-1)
    if audio.size > n_chunks * chunk_size:
        out[n_chunks * chunk_size :] = audio[n_chunks * chunk_size :]
    return out


def extract(
    audio: np.ndarray,
    sr: int,
    *,
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    seed: str = DEFAULT_SEED,
    band: tuple[float, float] = DEFAULT_BAND_HZ,
    n_bins: int = DEFAULT_BINS_PER_CHUNK,
) -> ExtractResult:
    """
    Attempt to extract a watermark from `audio`. Always returns an
    ExtractResult — sets `found=False` on failure rather than raising.
    """
    if audio.ndim != 1:
        return ExtractResult(False, None, None, 0, 0, None, None, error="audio must be 1-D mono")

    chunk_size = int(round(chunk_seconds * sr))
    chunked, n_chunks = _split_chunks(audio, chunk_size)
    if n_chunks < PAYLOAD_BITS:
        return ExtractResult(False, None, None, n_chunks, 0, None, None,
                             error=f"need {PAYLOAD_BITS}+ chunks, got {n_chunks}")

    soft = np.empty(n_chunks, dtype=np.float64)
    for i in range(n_chunks):
        soft[i] = detect_chunk(chunked[i], sr, seed=seed, band=band, n_bins=n_bins)

    # The 8-bit sync prefix isn't unique — a payload can coincidentally
    # contain the sync pattern at some other position, scoring nearly as
    # high as the true alignment. Walk candidates by descending sync score
    # and accept the first whose CRC validates. CRC-12's 1-in-4096 false-
    # positive rate makes this safe over 56 trials.
    candidates = rank_alignments(soft)
    last_error = "no candidate alignment produced a valid CRC"
    for offset, score in candidates:
        hard = majority_decode(soft, offset)
        if hard is None:
            continue
        try:
            version, shortcode_int = unpack_payload(hard)
        except PayloadError as exc:
            last_error = str(exc)
            continue
        n_reps = max(1, n_chunks // PAYLOAD_BITS)
        return ExtractResult(
            found=True,
            shortcode_int=shortcode_int,
            version=version,
            n_chunks=n_chunks,
            n_repetitions=n_reps,
            sync_offset=offset,
            sync_score=score,
        )

    top_offset, top_score = candidates[0] if candidates else (None, None)
    return ExtractResult(
        found=False,
        shortcode_int=None,
        version=None,
        n_chunks=n_chunks,
        n_repetitions=max(1, n_chunks // PAYLOAD_BITS),
        sync_offset=top_offset,
        sync_score=top_score,
        error=last_error,
    )
