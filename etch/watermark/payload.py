"""
Watermark payload — packing, CRC-12, sync pattern.

Wire format (56 bits per repetition):
    [ 8b sync 0xAC ][ 4b version ][ 32b shortcode_int ][ 12b CRC-12 ]

The CRC covers the version + shortcode_int (36 bits, packed MSB-first).
The shortcode is encoded as the raw 32-bit proof_id portion of the Crockford
shortcode (the 8-bit checksum part isn't needed here — the CRC plays the same
role inside the watermark, and rejection on lookup catches the rest).

Sync pattern is a fixed 8-bit prefix; the decoder correlates this across all
56 possible chunk-offset alignments to find payload start in a re-encoded /
trimmed audio file.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

PAYLOAD_BITS = 56
SYNC_BITS = 8
VERSION_BITS = 4
SHORTCODE_BITS = 32
CRC_BITS = 12

SYNC_PATTERN = 0xAC  # 0b10101100 — balanced 0/1, no DC bias
WATERMARK_VERSION = 1


class PayloadError(ValueError):
    """Raised when a payload fails to unpack or CRC-validate."""


# ---------------------------------------------------------------------------
# CRC-12 — polynomial 0x80F (x^12 + x^11 + x^3 + x^2 + x + 1).
# 12 bits is enough to make accidental matches at ~1/4096, well below the
# false-positive rate we care about given the sync prefix is also checked.
# ---------------------------------------------------------------------------

_CRC12_POLY = 0x80F
_CRC12_INIT = 0xFFF


def crc12(data_bits: int, n_bits: int) -> int:
    """Compute CRC-12 over the n_bits low bits of data_bits, MSB first."""
    if n_bits < 0:
        raise ValueError("n_bits must be non-negative")
    reg = _CRC12_INIT
    for i in range(n_bits - 1, -1, -1):
        bit = (data_bits >> i) & 1
        top = (reg >> 11) & 1
        reg = ((reg << 1) | bit) & 0xFFF
        if top:
            reg ^= _CRC12_POLY
    return reg & 0xFFF


# ---------------------------------------------------------------------------
# Pack / unpack
# ---------------------------------------------------------------------------

def pack_payload(shortcode_int: int, version: int = WATERMARK_VERSION) -> np.ndarray:
    """
    Pack a 56-bit payload into a uint8 array of bits (MSB-first within payload).

    Returns: shape (PAYLOAD_BITS,), dtype=uint8, values in {0, 1}.
    """
    if not 0 <= shortcode_int <= 0xFFFFFFFF:
        raise ValueError("shortcode_int must fit in 32 bits")
    if not 0 <= version <= 0xF:
        raise ValueError("version must fit in 4 bits")

    body = (version << SHORTCODE_BITS) | shortcode_int  # 36 bits
    crc = crc12(body, VERSION_BITS + SHORTCODE_BITS)

    payload = (SYNC_PATTERN << (VERSION_BITS + SHORTCODE_BITS + CRC_BITS))
    payload |= body << CRC_BITS
    payload |= crc

    bits = np.zeros(PAYLOAD_BITS, dtype=np.uint8)
    for i in range(PAYLOAD_BITS):
        bits[i] = (payload >> (PAYLOAD_BITS - 1 - i)) & 1
    return bits


def unpack_payload(bits: Sequence[int]) -> tuple[int, int]:
    """
    Unpack a 56-bit payload bit array into (version, shortcode_int).

    Verifies sync prefix and CRC. Raises PayloadError on any mismatch.
    """
    if len(bits) != PAYLOAD_BITS:
        raise PayloadError(f"expected {PAYLOAD_BITS} bits, got {len(bits)}")

    val = 0
    for b in bits:
        val = (val << 1) | int(b & 1)

    sync = (val >> (PAYLOAD_BITS - SYNC_BITS)) & 0xFF
    if sync != SYNC_PATTERN:
        raise PayloadError(f"sync mismatch: got {sync:#04x}, want {SYNC_PATTERN:#04x}")

    body = (val >> CRC_BITS) & ((1 << (VERSION_BITS + SHORTCODE_BITS)) - 1)
    got_crc = val & ((1 << CRC_BITS) - 1)
    want_crc = crc12(body, VERSION_BITS + SHORTCODE_BITS)
    if got_crc != want_crc:
        raise PayloadError(f"CRC mismatch: got {got_crc:#05x}, want {want_crc:#05x}")

    version = (body >> SHORTCODE_BITS) & 0xF
    shortcode_int = body & 0xFFFFFFFF
    return version, shortcode_int


def payload_bits_repeated(bits: np.ndarray, n_chunks: int) -> np.ndarray:
    """
    Tile a 56-bit payload to fill n_chunks chunks.

    Returns shape (n_chunks,), the bit to embed in each chunk.
    """
    if bits.shape != (PAYLOAD_BITS,):
        raise ValueError(f"bits must be shape ({PAYLOAD_BITS},), got {bits.shape}")
    out = np.empty(n_chunks, dtype=np.uint8)
    for i in range(n_chunks):
        out[i] = bits[i % PAYLOAD_BITS]
    return out
