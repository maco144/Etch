"""
Etch Assent — encrypted document storage for send-to-sign (V2 slice 1).

This router stores opaque ciphertext blobs uploaded by the browser. The plaintext
(PDF bytes) is never visible to this service: the sender generates an AES-256-GCM
key client-side, encrypts the PDF, and uploads the ciphertext here. The key
travels out-of-band as a URL fragment (`…#key=<b64>`), which browsers don't send
in HTTP requests — so Etch literally cannot decrypt any document it's holding.

Endpoints (anonymous, rate-limited):

    POST /v1/assent/document            → { document_id }  upload ciphertext
    GET  /v1/assent/document/{doc_id}   → application/octet-stream
    PUT  /v1/assent/document/{doc_id}   → 204  replace (for signed version)
    HEAD /v1/assent/document/{doc_id}   → 200/404 (exists check; cheap)

Storage: local disk at ``ETCH_ASSENT_DOC_DIR`` (default ``/var/etch/assent-documents``).
Migration to Cloudflare R2 is a helper swap on ``_read`` / ``_write`` — the HTTP
surface doesn't change.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .assent_api import _SlidingWindowLimiter, _client_ip

logger = logging.getLogger(__name__)

assent_docs_router = APIRouter(tags=["Etch Assent — Documents"])


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Encrypted PDF bytes: 10 MB plaintext cap from V1 + AES-GCM overhead (IV + tag)
# + room for signed re-uploads that add a signature image. 15 MB covers it.
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024

DOC_DIR = Path(os.environ.get("ETCH_ASSENT_DOC_DIR", "/var/etch/assent-documents"))

# Uploads are heavier than chain stamps, so a tighter per-IP window.
UPLOAD_WINDOW_SECONDS = 3600
UPLOAD_MAX_REQUESTS = 10
_upload_limiter = _SlidingWindowLimiter(UPLOAD_WINDOW_SECONDS, UPLOAD_MAX_REQUESTS)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class UploadResponse(BaseModel):
    object: str = "assent.document"
    document_id: str = Field(description="Opaque handle for the ciphertext")
    size: int = Field(description="Bytes stored")


# ---------------------------------------------------------------------------
# Storage helpers — keep the IO surface tiny so R2 can drop in later
# ---------------------------------------------------------------------------

def _new_doc_id() -> str:
    # 128 bits of entropy. Big enough that enumeration is infeasible, so the
    # existence of a doc_id being discoverable is fine on its own.
    return secrets.token_urlsafe(24)


def _doc_path(doc_id: str) -> Path:
    # Defense-in-depth against path traversal: reject anything that isn't the
    # flat token we issue. token_urlsafe uses [A-Za-z0-9_-] only.
    if not doc_id or not all(c.isalnum() or c in "_-" for c in doc_id):
        raise HTTPException(status_code=400, detail="invalid document_id")
    return DOC_DIR / doc_id


def _ensure_dir() -> None:
    DOC_DIR.mkdir(parents=True, exist_ok=True)


def _read(doc_id: str) -> bytes:
    path = _doc_path(doc_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"document {doc_id} not found")
    return path.read_bytes()


def _write(doc_id: str, data: bytes) -> None:
    _ensure_dir()
    path = _doc_path(doc_id)
    # Atomic-ish write: write to tmp, rename. Prevents a half-written file
    # from being served if the process is killed mid-upload.
    tmp = path.with_suffix(".partial")
    tmp.write_bytes(data)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Request body helpers
# ---------------------------------------------------------------------------

async def _read_body(request: Request) -> bytes:
    """Read the raw request body, enforcing the size cap."""
    body = await request.body()
    if len(body) == 0:
        raise HTTPException(status_code=422, detail="empty body")
    if len(body) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"document exceeds {MAX_DOCUMENT_BYTES} bytes",
        )
    return body


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@assent_docs_router.post(
    "/v1/assent/document",
    summary="Upload an encrypted document (anonymous)",
    response_model=UploadResponse,
)
async def upload_document(request: Request) -> UploadResponse:
    """Store an opaque ciphertext blob and return a ``document_id`` the uploader
    can share via a URL fragment key."""
    ip = _client_ip(request)
    if not _upload_limiter.check(ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: max {UPLOAD_MAX_REQUESTS} uploads per IP per hour",
            headers={"Retry-After": str(UPLOAD_WINDOW_SECONDS)},
        )

    data = await _read_body(request)
    doc_id = _new_doc_id()
    try:
        _write(doc_id, data)
    except OSError as exc:
        logger.warning(f"[Etch] document write failed: {exc}")
        raise HTTPException(status_code=503, detail="Document store unavailable")

    logger.info(f"[Etch] assent document {doc_id} stored ({len(data)} bytes)")
    return UploadResponse(document_id=doc_id, size=len(data))


@assent_docs_router.get(
    "/v1/assent/document/{document_id}",
    summary="Download an encrypted document (anonymous)",
)
async def download_document(document_id: str) -> Response:
    data = _read(document_id)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(data)),
            # Short cache — ciphertext for a given doc_id can change when the
            # signed version is PUT back. Revalidate on every request.
            "Cache-Control": "no-cache, must-revalidate",
        },
    )


@assent_docs_router.head(
    "/v1/assent/document/{document_id}",
    summary="Check whether an encrypted document exists",
)
async def head_document(document_id: str) -> Response:
    path = _doc_path(document_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return Response(status_code=200, headers={"Content-Length": str(path.stat().st_size)})


@assent_docs_router.put(
    "/v1/assent/document/{document_id}",
    summary="Replace an encrypted document (signed version)",
    status_code=204,
)
async def replace_document(document_id: str, request: Request) -> Response:
    """After the recipient signs and re-encrypts, they PUT the new ciphertext
    back to the same ``document_id``. The fragment key is reused so the sender
    can still decrypt with their original link."""
    ip = _client_ip(request)
    if not _upload_limiter.check(ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: max {UPLOAD_MAX_REQUESTS} uploads per IP per hour",
            headers={"Retry-After": str(UPLOAD_WINDOW_SECONDS)},
        )

    # Must exist before we allow a replace — keeps PUT from being used as a
    # disguised upload that bypasses the normal path.
    path = _doc_path(document_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"document {document_id} not found")

    data = await _read_body(request)
    try:
        _write(document_id, data)
    except OSError as exc:
        logger.warning(f"[Etch] document replace failed: {exc}")
        raise HTTPException(status_code=503, detail="Document store unavailable")

    logger.info(f"[Etch] assent document {document_id} replaced ({len(data)} bytes)")
    return Response(status_code=204)


__all__ = [
    "assent_docs_router",
    "DOC_DIR",
    "MAX_DOCUMENT_BYTES",
    "_upload_limiter",
]
