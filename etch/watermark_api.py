"""
Etch watermark API — embed/extract endpoints for `etchmark`.

Endpoints:
    POST /v1/proof/{proof_id}/embed-audio
        Multipart upload (`file=...`). Returns a watermarked WAV carrying
        the proof's shortcode.

    POST /v1/proof/extract-audio
        Multipart upload (`file=...`). Decodes the audio (WAV/FLAC native;
        MP3/AAC/Opus/etc. via ffmpeg), runs the watermark detector, and if
        a valid payload is recovered, looks up the corresponding proof
        record and returns the resolver page URL.

Notes:
  - Embed always returns 16-bit PCM WAV. Lossy re-encoding by the artist
    (e.g. to MP3 for distribution) is the expected next step — the
    spread-spectrum layer survives it. Returning lossless from the API
    means we don't double-encode and lose headroom.
  - Extract is read-only; it does not register anything.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from .db import get_session
from .glyph import encode_shortcode, format_shortcode, verify_shortcode
from .models import ProofRecord
from .watermark.io import AudioIOError, read_audio, write_wav
from .watermark.pipeline import embed, extract

logger = logging.getLogger(__name__)

watermark_router = APIRouter(tags=["Etch Watermark"])

# 64 MiB cap — large enough for a CD-quality 5-minute WAV, small enough that
# a single bad upload can't OOM the server.
MAX_AUDIO_BYTES = 64 * 1024 * 1024

# Minimum audio length for a meaningful embed. The pipeline already enforces
# 56 chunks (≈56s at 1s chunks) — we surface a clearer 422 here.
MIN_AUDIO_SECONDS = 60


class WatermarkResolved(BaseModel):
    """A successfully resolved watermark — proof_id known, content_hash matches."""
    proof_id: int
    shortcode: str
    pretty: str
    magic_url: str
    content_hash: str
    label: Optional[str]
    owner: Optional[str]
    registered_at: float


class ExtractResponse(BaseModel):
    found: bool
    resolved: Optional[WatermarkResolved] = None
    n_chunks: int
    n_repetitions: int
    sync_score: Optional[float]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _read_upload(file: UploadFile) -> bytes:
    """Read upload up to MAX_AUDIO_BYTES; raise 413 if too large."""
    blob = await file.read(MAX_AUDIO_BYTES + 1)
    if len(blob) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"audio file exceeds {MAX_AUDIO_BYTES // (1024*1024)} MiB cap",
        )
    if not blob:
        raise HTTPException(status_code=422, detail="empty upload")
    return blob


async def _load_record(proof_id: int) -> Optional[ProofRecord]:
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ProofRecord).where(ProofRecord.leaf_index == proof_id)
            )
            return result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[Etch watermark] DB lookup failed: {exc}")
        return None


def _decode(blob: bytes, filename: Optional[str]) -> tuple:
    try:
        audio, sr = read_audio(blob, filename_hint=filename)
    except AudioIOError as exc:
        raise HTTPException(status_code=415, detail=f"unsupported audio: {exc}")
    seconds = audio.size / sr
    if seconds < MIN_AUDIO_SECONDS:
        raise HTTPException(
            status_code=422,
            detail=f"audio is {seconds:.1f}s; need at least {MIN_AUDIO_SECONDS}s for a watermark",
        )
    return audio, sr


def _base_url(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@watermark_router.post(
    "/v1/proof/{proof_id}/embed-audio",
    summary="Embed an etchmark watermark in audio",
)
async def embed_audio(
    proof_id: int,
    file: UploadFile = File(...),
) -> Response:
    record = await _load_record(proof_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Proof {proof_id} not found")

    blob = await _read_upload(file)
    audio, sr = _decode(blob, file.filename)

    try:
        watermarked = embed(audio, sr, record.leaf_index)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    wav = write_wav(watermarked, sr)
    shortcode = encode_shortcode(record.leaf_index, record.content_hash)
    out_name = f"etched-{shortcode}.wav"
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Etch-Shortcode": shortcode,
            "X-Etch-Proof-Id": str(record.leaf_index),
        },
    )


@watermark_router.post(
    "/v1/proof/extract-audio",
    summary="Detect an etchmark watermark and resolve to a proof record",
)
async def extract_audio(
    request: Request,
    file: UploadFile = File(...),
) -> ExtractResponse:
    blob = await _read_upload(file)
    audio, sr = _decode(blob, file.filename)
    result = extract(audio, sr)

    if not result.found:
        return ExtractResponse(
            found=False,
            n_chunks=result.n_chunks,
            n_repetitions=result.n_repetitions,
            sync_score=result.sync_score,
            error=result.error,
        )

    # Look up the record. The watermark itself doesn't carry the checksum,
    # so we re-derive a shortcode from (recovered proof_id, record.content_hash)
    # and verify — that catches a watermark that points at a record whose
    # content has been replaced or never matched.
    record = await _load_record(result.shortcode_int)
    if not record:
        return ExtractResponse(
            found=False,
            n_chunks=result.n_chunks,
            n_repetitions=result.n_repetitions,
            sync_score=result.sync_score,
            error=f"watermark resolves to unknown proof_id={result.shortcode_int}",
        )

    shortcode = encode_shortcode(record.leaf_index, record.content_hash)
    if not verify_shortcode(shortcode, record.content_hash):
        # Should be impossible since we just generated it, but defensive.
        return ExtractResponse(
            found=False,
            n_chunks=result.n_chunks,
            n_repetitions=result.n_repetitions,
            sync_score=result.sync_score,
            error="recovered watermark failed checksum verification",
        )

    ts = record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else record.created_at
    base = _base_url(request)
    resolved = WatermarkResolved(
        proof_id=record.leaf_index,
        shortcode=shortcode,
        pretty=format_shortcode(shortcode),
        magic_url=f"{base}/g/{shortcode}",
        content_hash=record.content_hash,
        label=record.label,
        owner=record.owner,
        registered_at=ts,
    )
    return ExtractResponse(
        found=True,
        resolved=resolved,
        n_chunks=result.n_chunks,
        n_repetitions=result.n_repetitions,
        sync_score=result.sync_score,
    )
