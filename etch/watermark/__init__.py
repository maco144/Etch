"""
Etch audio watermark — `etchmark`.

A robust, inaudible audio watermark that carries an Etch shortcode. Designed
as a *pointer* to an on-chain proof record, not as a proof in itself: the
chain remains the source of truth, the watermark just lets verifiers find
which Etch record claims a given recording.

Modules:
    payload  — bit packing, CRC-12, sync pattern
    sync     — chunk alignment / sync recovery
    spread   — spread-spectrum FFT layer
    pipeline — high-level embed() / extract()
"""
from .payload import (
    PAYLOAD_BITS,
    SYNC_PATTERN,
    SYNC_BITS,
    pack_payload,
    unpack_payload,
    payload_bits_repeated,
    PayloadError,
)

__all__ = [
    "PAYLOAD_BITS",
    "SYNC_PATTERN",
    "SYNC_BITS",
    "pack_payload",
    "unpack_payload",
    "payload_bits_repeated",
    "PayloadError",
]
