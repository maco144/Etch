"""
Watermark sync — find payload start in a stream of recovered bit estimates.

The encoder tiles a 56-bit payload across all chunks: encoder writes
soft_bits[k] = payload_bits[(k + trim) mod 56] where `trim` is how many
chunks were lost from the start (e.g. by re-encoding pre-roll or a manual
trim). We recover `trim` by sliding the sync pattern across all 56 possible
modular alignments and picking the one that maximizes correlation.

Decoding then uses *modular* deinterleaving — every chunk contributes to
exactly one of the 56 payload bit positions, so even partial repetitions
add evidence. This makes the system work on audio as short as ~57s at 1s
chunks.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .payload import PAYLOAD_BITS, SYNC_BITS, SYNC_PATTERN


def _sync_signs() -> np.ndarray:
    """SYNC_PATTERN as ±1 array, MSB-first. Used as correlation template."""
    out = np.empty(SYNC_BITS, dtype=np.float64)
    for i in range(SYNC_BITS):
        out[i] = 1.0 if (SYNC_PATTERN >> (SYNC_BITS - 1 - i)) & 1 else -1.0
    return out


def find_alignment(soft_bits: np.ndarray) -> tuple[int, float]:
    """
    Find the modular trim offset `t` ∈ [0, 56) such that:
        soft_bits[k] ≈ payload_bits[(k + t) mod 56]

    The score is the per-chunk-normalized correlation of the sync prefix
    (8 bits at payload positions 0..7) against the soft stream — higher is
    a more confident lock.
    """
    offsets = rank_alignments(soft_bits)
    return offsets[0]


def rank_alignments(soft_bits: np.ndarray) -> list[tuple[int, float]]:
    """
    Return all 56 candidate alignments sorted by sync score (descending).

    The sync prefix is only 8 bits, which means a payload that happens to
    contain the 8-bit sync pattern at some other position will score nearly
    as high as the true alignment. The caller should iterate this list and
    accept the first alignment whose decoded payload passes CRC.
    """
    if soft_bits.size < PAYLOAD_BITS:
        raise ValueError(f"need at least {PAYLOAD_BITS} chunks, got {soft_bits.size}")

    template = _sync_signs()
    n = soft_bits.size
    k = np.arange(n)

    scored: list[tuple[int, float]] = []
    for t in range(PAYLOAD_BITS):
        payload_pos = (k + t) % PAYLOAD_BITS
        mask = payload_pos < SYNC_BITS
        if not mask.any():
            continue
        idx = payload_pos[mask]
        contribution = soft_bits[mask] * template[idx]
        scored.append((t, float(contribution.sum() / mask.sum())))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def majority_decode(soft_bits: np.ndarray, offset: int) -> Optional[np.ndarray]:
    """
    Modular deinterleave: every chunk contributes to its corresponding payload
    bit position. Returns 56 hard bits, or None if there isn't enough evidence
    (we require every payload position to have received at least one sample).
    """
    if offset < 0 or offset >= PAYLOAD_BITS:
        raise ValueError(f"offset out of range: {offset}")
    n = soft_bits.size
    if n == 0:
        return None

    accum = np.zeros(PAYLOAD_BITS, dtype=np.float64)
    counts = np.zeros(PAYLOAD_BITS, dtype=np.int64)
    k = np.arange(n)
    payload_pos = (k + offset) % PAYLOAD_BITS
    np.add.at(accum, payload_pos, soft_bits)
    np.add.at(counts, payload_pos, 1)

    if (counts == 0).any():
        return None
    return (accum > 0).astype(np.uint8)
