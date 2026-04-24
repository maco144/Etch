"""
Etch Assent — public, anonymous-write endpoints for the client-side PDF signer.

This router exposes a narrowly-scoped surface that lets the Assent web app
commit signing events to Etch without an API key. All writes are pinned to
the well-known namespace ``assent/public`` and rate-limited per client IP.

The authenticated SoR API (:mod:`etch.records_api`) remains the canonical way
to read the chain — ``GET /v1/records`` and ``/v1/records/{id}/proof`` both
require a Bearer token. To keep the Assent flow fully anonymous, this router
mirrors the key read surfaces for ``assent/public`` only:

    POST /v1/assent/stamp                      — append an assent.event record
    GET  /v1/assent/chain/{document_id}         — event chain for a document
    GET  /v1/assent/records/{record_id}         — single receipt
    GET  /v1/assent/records/{record_id}/proof   — offline-verifiable proof
    GET  /v1/assent/verify?hash={sha256_hex}    — find events by document hash

Rate limit: 20 writes per IP per hour (in-memory sliding window).

Deployment note: run with ``uvicorn --workers 1``. The limiter is per-process,
so multiple workers multiply the effective cap.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import deque
from threading import Lock
from typing import Any, Dict, Deque, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .chain_manager import get_chain_manager
from .db import get_session
from .models import Namespace, RecordEntry, generate_record_id

logger = logging.getLogger(__name__)

assent_router = APIRouter(tags=["Etch Assent"])


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ASSENT_NAMESPACE_ID = "assent_public"
ASSENT_NAMESPACE_NAME = "assent/public"

RATE_LIMIT_WINDOW_SECONDS = 3600
RATE_LIMIT_MAX_REQUESTS = 20

# Bumped from 16KB → 32KB so WebAuthn attestation blobs (typically 4–8KB, up to
# ~20KB for some authenticators) fit without truncation.
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_EVENTS_PER_DOCUMENT = 32

# "uploaded" is emitted by the sender in the send-to-sign (V2) flow the moment
# they push ciphertext to /v1/assent/document. It carries the plaintext SHA-256
# the sender saw locally, so the chain later binds the ciphertext-the-server-
# held to the plaintext-the-recipient-signed. Without it, an adversarial sender
# could claim after the fact that a different PDF was the one they sent.
VALID_EVENT_TYPES = {"uploaded", "created", "field_added", "signed", "countersigned", "finalized"}

# Salt for the per-IP bookkeeping hash. Kept in an env var so it can be rotated
# in prod without a code change (rotation invalidates old hashes, which is the
# whole point — we never want to be able to link present and past activity).
_IP_SALT = os.environ.get("ETCH_ASSENT_IP_SALT", "etch-assent")


# ---------------------------------------------------------------------------
# IP rate limiter (in-memory, sliding window)
# ---------------------------------------------------------------------------

class _SlidingWindowLimiter:
    """Per-key sliding window counter. Not durable across restarts — fine for V1."""

    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self._window = window_seconds
        self._max = max_requests
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = Lock()

    def check(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_limiter = _SlidingWindowLimiter(RATE_LIMIT_WINDOW_SECONDS, RATE_LIMIT_MAX_REQUESTS)


def _client_ip(request: Request) -> str:
    # Respect an X-Forwarded-For header placed by our reverse proxy (Caddy).
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host


# ---------------------------------------------------------------------------
# Namespace bootstrap
# ---------------------------------------------------------------------------

async def ensure_assent_namespace() -> None:
    """Idempotently create the public assent namespace at startup."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Namespace).where(Namespace.namespace_id == ASSENT_NAMESPACE_ID)
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(Namespace(namespace_id=ASSENT_NAMESPACE_ID, name=ASSENT_NAMESPACE_NAME))
                logger.info(f"[Etch] Bootstrapped namespace {ASSENT_NAMESPACE_ID}")
    except Exception as exc:
        logger.warning(f"[Etch] Assent namespace bootstrap failed: {exc}")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AssentEvent(BaseModel):
    kind: str = Field(default="assent.event", description="Record kind — must be 'assent.event'")
    schema_version: int = Field(default=1, ge=1)
    document_id: str = Field(min_length=4, max_length=128, description="Stable document identifier")
    event_type: str = Field(description="created | field_added | signed | countersigned | finalized")
    document_hash: str = Field(description="SHA-256 hex (64) of the PDF bytes after this event")
    parent_hash: Optional[str] = Field(None, description="document_hash of the previous event")
    event_index: int = Field(ge=0)
    signer: Optional[Dict[str, Any]] = None
    location: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = Field(None, description="Client-provided ISO 8601 timestamp")
    client_metadata: Optional[Dict[str, Any]] = None


class AssentReceipt(BaseModel):
    id: str
    object: str = "assent.receipt"
    document_id: str
    event_type: str
    event_index: int
    document_hash: str
    parent_hash: Optional[str] = None
    leaf_hash: str
    mmr_root: str
    chain_position: int
    namespace: str = ASSENT_NAMESPACE_NAME
    server_timestamp: float
    client_timestamp: Optional[str] = None
    verification_url: str
    signer: Optional[Dict[str, Any]] = None
    location: Optional[Dict[str, Any]] = None


class AssentChainResponse(BaseModel):
    object: str = "assent.chain"
    document_id: str
    namespace: str = ASSENT_NAMESPACE_NAME
    events: List[AssentReceipt]
    event_count: int
    chain_intact: bool


class AssentVerifyResponse(BaseModel):
    object: str = "assent.verify"
    hash: str = Field(description="SHA-256 hex queried")
    match_count: int = Field(description="Number of events with this document_hash")
    document_ids: List[str] = Field(description="Unique document_ids across matches")
    events: List[AssentReceipt] = Field(description="Matching events, ordered by chain position")


class AssentInclusionProof(BaseModel):
    """Offline-verifiable inclusion proof. Any party can reconstruct the
    leaf_hash and mmr_root from the published fields, without ever talking
    to Etch. Mirrors ``/v1/records/{id}/proof`` but pinned to the public
    assent namespace and anonymous."""

    object: str = "inclusion_proof"
    record_id: str
    leaf_index: int
    leaf_hash: str
    mmr_root: str
    prev_root: str
    payload_hash: str
    timestamp: float
    algorithm: str = "sha256"
    verification_steps: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_hex(value: str, field: str) -> None:
    if len(value) != 64:
        raise HTTPException(status_code=422, detail=f"{field} must be 64-character SHA-256 hex")
    try:
        int(value, 16)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be hexadecimal")


def _event_to_receipt(record: RecordEntry) -> AssentReceipt:
    meta = json.loads(record.metadata_json) if record.metadata_json else {}
    event = meta.get("event", {}) if isinstance(meta, dict) else {}
    return AssentReceipt(
        id=record.record_id,
        document_id=event.get("document_id", record.external_id or ""),
        event_type=event.get("event_type", ""),
        event_index=int(event.get("event_index", 0)),
        document_hash=event.get("document_hash", record.record_hash),
        parent_hash=event.get("parent_hash"),
        leaf_hash=record.leaf_hash,
        mmr_root=record.mmr_root,
        chain_position=record.leaf_index,
        server_timestamp=record.created_at_exact or record.created_at.timestamp(),
        client_timestamp=event.get("timestamp"),
        verification_url=f"/verify/{record.record_id}",
        signer=event.get("signer"),
        location=event.get("location"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@assent_router.post("/v1/assent/stamp", summary="Commit an assent event (anonymous)")
async def stamp_event(event: AssentEvent, request: Request) -> AssentReceipt:
    """
    Anonymously append an Etch Assent event to the ``assent/public`` namespace.

    No API key required. Rate limited per client IP. PDF contents never hit this
    endpoint — only hashes and event metadata.
    """
    if event.kind != "assent.event":
        raise HTTPException(status_code=422, detail="kind must be 'assent.event'")
    if event.event_type not in VALID_EVENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"event_type must be one of {sorted(VALID_EVENT_TYPES)}",
        )

    _validate_hex(event.document_hash, "document_hash")
    if event.parent_hash is not None:
        _validate_hex(event.parent_hash, "parent_hash")
    if event.event_index == 0 and event.parent_hash is not None:
        raise HTTPException(status_code=422, detail="first event must not have a parent_hash")
    if event.event_index > 0 and event.parent_hash is None:
        raise HTTPException(status_code=422, detail="non-first events require a parent_hash")

    # Cap JSON payload size to prevent obvious abuse
    raw = event.model_dump_json()
    if len(raw.encode()) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="event payload too large")

    ip = _client_ip(request)
    if not _limiter.check(ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: max {RATE_LIMIT_MAX_REQUESTS} events per IP per hour",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )

    # Count existing events for this document and enforce two invariants in
    # one pass:
    #   * event_index == existing_count  → monotonic, no document_id squatting
    #   * existing_count < MAX_EVENTS    → blast-radius ceiling
    try:
        async with get_session() as session:
            existing = await session.execute(
                select(func.count())
                .select_from(RecordEntry)
                .where(
                    RecordEntry.namespace_id == ASSENT_NAMESPACE_ID,
                    RecordEntry.external_id == event.document_id,
                )
            )
            existing_count = existing.scalar() or 0

        if existing_count >= MAX_EVENTS_PER_DOCUMENT:
            raise HTTPException(
                status_code=409,
                detail=f"document {event.document_id} exceeds {MAX_EVENTS_PER_DOCUMENT} events",
            )
        if event.event_index != existing_count:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"event_index {event.event_index} does not match expected "
                    f"{existing_count} for document {event.document_id}"
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[Etch] assent count check failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    payload = {
        "record_hash": event.document_hash,
        "record_type": "assent.event",
        "external_id": event.document_id,
        "namespace": ASSENT_NAMESPACE_ID,
        "registered_at": time.time(),
    }

    manager = get_chain_manager()
    chain = await manager.get_chain(ASSENT_NAMESPACE_ID)
    entry = chain.append(
        action_type="record_commit",
        payload=payload,
        specialist="etch",
        agent_id=event.document_hash,
    )

    rec_id = generate_record_id()
    metadata = {"event": event.model_dump(exclude_none=True), "ip_hash": _hash_ip(ip)}

    try:
        async with get_session() as session:
            session.add(RecordEntry(
                record_id=rec_id,
                namespace_id=ASSENT_NAMESPACE_ID,
                leaf_index=entry.leaf_index,
                leaf_hash=entry.leaf_hash,
                mmr_root=entry.mmr_root,
                chain_depth=entry.leaf_index + 1,
                payload_hash=entry.payload_hash,
                record_type="assent.event",
                external_id=event.document_id,
                record_hash=event.document_hash,
                metadata_json=json.dumps(metadata),
                created_at_exact=entry.created_at,
            ))
    except Exception as exc:
        logger.warning(f"[Etch] Assent persist failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    logger.info(
        f"[Etch] Assent event {event.event_type} for {event.document_id} "
        f"committed to {ASSENT_NAMESPACE_ID} chain_pos={entry.leaf_index}"
    )

    return AssentReceipt(
        id=rec_id,
        document_id=event.document_id,
        event_type=event.event_type,
        event_index=event.event_index,
        document_hash=event.document_hash,
        parent_hash=event.parent_hash,
        leaf_hash=entry.leaf_hash,
        mmr_root=entry.mmr_root,
        chain_position=entry.leaf_index,
        server_timestamp=entry.created_at,
        client_timestamp=event.timestamp,
        verification_url=f"/verify/{rec_id}",
        signer=event.signer,
        location=event.location,
    )


@assent_router.get(
    "/v1/assent/chain/{document_id}",
    summary="Fetch the event chain for a document (public)",
)
async def get_document_chain(document_id: str) -> AssentChainResponse:
    """
    Return every event recorded for ``document_id`` in ``assent/public``, ordered
    by chain position. Anyone with the document ID can read this.
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(RecordEntry)
                .where(
                    RecordEntry.namespace_id == ASSENT_NAMESPACE_ID,
                    RecordEntry.external_id == document_id,
                )
                .order_by(RecordEntry.leaf_index.asc())
            )
            rows = list(result.scalars().all())
    except Exception as exc:
        logger.warning(f"[Etch] assent chain lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not rows:
        raise HTTPException(status_code=404, detail=f"no events found for document {document_id}")

    receipts = [_event_to_receipt(r) for r in rows]

    # Chain integrity: parent_hash[N] must equal document_hash[N-1]
    chain_intact = True
    for i, receipt in enumerate(receipts):
        if receipt.event_index != i:
            chain_intact = False
            break
        if i == 0 and receipt.parent_hash is not None:
            chain_intact = False
            break
        if i > 0 and receipt.parent_hash != receipts[i - 1].document_hash:
            chain_intact = False
            break

    return AssentChainResponse(
        document_id=document_id,
        events=receipts,
        event_count=len(receipts),
        chain_intact=chain_intact,
    )


@assent_router.get(
    "/v1/assent/records/{record_id}",
    summary="Fetch a single assent receipt by ID (public)",
)
async def get_assent_record(record_id: str) -> AssentReceipt:
    try:
        async with get_session() as session:
            result = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.record_id == record_id,
                    RecordEntry.namespace_id == ASSENT_NAMESPACE_ID,
                )
            )
            record = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[Etch] assent record lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    if record is None:
        raise HTTPException(status_code=404, detail=f"record {record_id} not found")

    return _event_to_receipt(record)


@assent_router.get(
    "/v1/assent/records/{record_id}/proof",
    summary="Self-contained inclusion proof for a public assent record",
)
async def get_assent_proof(record_id: str) -> AssentInclusionProof:
    """Return an offline-verifiable inclusion proof. The caller can reconstruct
    ``leaf_hash`` and ``mmr_root`` locally without ever talking to Etch — see
    ``verification_steps`` for the formula."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.record_id == record_id,
                    RecordEntry.namespace_id == ASSENT_NAMESPACE_ID,
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise HTTPException(status_code=404, detail=f"record {record_id} not found")

            prev = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.namespace_id == ASSENT_NAMESPACE_ID,
                    RecordEntry.leaf_index == record.leaf_index - 1,
                )
            )
            prev_record = prev.scalar_one_or_none()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[Etch] assent proof lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    prev_root = prev_record.mmr_root if prev_record else "0" * 64
    ts = record.created_at_exact or record.created_at.timestamp()

    return AssentInclusionProof(
        record_id=record_id,
        leaf_index=record.leaf_index,
        leaf_hash=record.leaf_hash,
        mmr_root=record.mmr_root,
        prev_root=prev_root,
        payload_hash=record.payload_hash,
        timestamp=ts,
        verification_steps=[
            "leaf_hash = SHA256(prev_root + ':' + 'record_commit' + ':' + payload_hash + ':' + timestamp)",
            "mmr_root = SHA256(prev_root + ':' + leaf_hash)",
        ],
    )


@assent_router.get(
    "/v1/assent/verify",
    summary="Find public assent events by document hash",
)
async def verify_by_hash(
    hash: str = Query(
        ...,
        description="SHA-256 hex (64 chars) of the PDF bytes to look up",
        min_length=64,
        max_length=64,
    ),
) -> AssentVerifyResponse:
    """Look up every ``assent/public`` event whose ``document_hash`` matches.

    This is the endpoint the recipient-side "verify" flow needs: given just the
    PDF bytes, hash them in the browser and call this to discover whether (and
    when, and by whom) the document was signed with Etch Assent.

    A 404 is returned if no events match — the frontend uses that to render the
    tamper/not-signed banner from the spec's anti-tamper UX.
    """
    _validate_hex(hash, "hash")

    try:
        async with get_session() as session:
            result = await session.execute(
                select(RecordEntry)
                .where(
                    RecordEntry.namespace_id == ASSENT_NAMESPACE_ID,
                    RecordEntry.record_hash == hash,
                )
                .order_by(RecordEntry.leaf_index.asc())
            )
            rows = list(result.scalars().all())
    except Exception as exc:
        logger.warning(f"[Etch] verify-by-hash lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="hash not found in Etch Assent chain",
        )

    receipts = [_event_to_receipt(r) for r in rows]
    # Deduplicate document_ids while preserving first-seen order.
    seen: Dict[str, bool] = {}
    for r in receipts:
        if r.document_id and r.document_id not in seen:
            seen[r.document_id] = True

    return AssentVerifyResponse(
        hash=hash,
        match_count=len(receipts),
        document_ids=list(seen.keys()),
        events=receipts,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _hash_ip(ip: str) -> str:
    """Store a salted hash instead of the raw IP (GDPR-lite). Salt is
    configurable via ``ETCH_ASSENT_IP_SALT`` so ops can rotate in prod."""
    return hashlib.sha256(f"{_IP_SALT}:{ip}".encode()).hexdigest()[:16]


__all__ = [
    "assent_router",
    "ensure_assent_namespace",
    "ASSENT_NAMESPACE_ID",
    "ASSENT_NAMESPACE_NAME",
    "_limiter",
]
