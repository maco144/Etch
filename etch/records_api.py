"""
Etch SoR API — /v1/records

System of Record provenance primitive. Every record committed through this API
gets a cryptographic receipt (Merkle MMR) proving it existed in a specific state
at a specific time.

Endpoints:
    POST /v1/records                - Create a record receipt
    GET  /v1/records                - List/filter records (cursor pagination)
    GET  /v1/records/{record_id}    - Retrieve receipt by record_id
    GET  /v1/records/{record_id}/proof - Self-contained inclusion proof
    POST /v1/records/verify         - Verify record against chain
    GET  /v1/chain/root             - Current chain state
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from .auth import AuthContext, require_auth
from .chain_manager import get_chain_manager
from .db import get_session
from .models import RecordEntry, generate_record_id

logger = logging.getLogger(__name__)

records_router = APIRouter(tags=["Etch Records"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RecordData(BaseModel):
    type: Optional[str] = Field(None, max_length=200, description="Freeform record type (e.g. salesforce.opportunity)")
    id: Optional[str] = Field(None, max_length=200, description="External record ID (e.g. 0065g00000XYZ)")
    data: Optional[Dict[str, Any]] = Field(None, description="Record payload — hashed, never stored")


class CreateRecordRequest(BaseModel):
    record: Optional[RecordData] = Field(None, description="Record to commit")
    record_hash: Optional[str] = Field(None, description="Pre-computed SHA-256 hex (64 chars) — use when data shouldn't leave your network")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata stored alongside the hash (actor, source, action)")


class RecordReceipt(BaseModel):
    id: str = Field(description="Etch record ID (rec_...)")
    object: str = "record"
    record_hash: str = Field(description="SHA-256 hex of the record data")
    leaf_hash: str = Field(description="Chain commitment hash")
    mmr_root: str = Field(description="MMR root after this record")
    chain_position: int = Field(description="Leaf index in the namespace chain")
    chain_depth: int = Field(description="Total records in namespace chain")
    timestamp: float = Field(description="Unix timestamp (seconds, UTC)")
    namespace: str = Field(description="Namespace ID")
    record_type: Optional[str] = None
    external_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    verification_url: Optional[str] = None


class InclusionProofResponse(BaseModel):
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


class VerifyRecordRequest(BaseModel):
    record_id: str = Field(description="Etch record ID to verify")
    record: Optional[RecordData] = Field(None, description="Record data to verify against")
    record_hash: Optional[str] = Field(None, description="Pre-computed SHA-256 hex to verify")


class VerifyRecordResponse(BaseModel):
    object: str = "verification"
    record_id: str
    content_match: bool
    chain_integrity: bool
    verified: bool = Field(description="True only when both content_match and chain_integrity pass")
    verified_at: float
    original_timestamp: float


class ChainStateResponse(BaseModel):
    object: str = "chain_state"
    mmr_root: str
    chain_depth: int
    namespace: str
    timestamp: float


class RecordListResponse(BaseModel):
    object: str = "list"
    data: List[RecordReceipt]
    has_more: bool
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _hash_record_data(data: Dict[str, Any]) -> str:
    """Deterministic hash of record data."""
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@records_router.post("/v1/records", summary="Create a record provenance receipt")
async def create_record(body: CreateRecordRequest, auth: AuthContext = Depends(require_auth)) -> RecordReceipt:
    """
    Commit a record to the Etch chain. The record data is hashed and discarded —
    only the SHA-256 hash is stored. Returns a cryptographic receipt.
    """
    # Resolve record hash
    if body.record_hash:
        if len(body.record_hash) != 64:
            raise HTTPException(status_code=422, detail="record_hash must be a 64-character SHA-256 hex string")
        record_hash = body.record_hash
    elif body.record and body.record.data:
        record_hash = _hash_record_data(body.record.data)
    else:
        raise HTTPException(status_code=422, detail="Provide either record.data or record_hash")

    record_type = body.record.type if body.record else None
    external_id = body.record.id if body.record else None

    # Build chain payload
    payload = {
        "record_hash": record_hash,
        "record_type": record_type or "",
        "external_id": external_id or "",
        "namespace": auth.namespace_id,
        "registered_at": time.time(),
    }

    # Append to namespace chain
    manager = get_chain_manager()
    chain = await manager.get_chain(auth.namespace_id)
    entry = chain.append(
        action_type="record_commit",
        payload=payload,
        specialist="etch",
        agent_id=record_hash,
    )

    # Generate record ID and persist
    rec_id = generate_record_id()
    metadata_str = json.dumps(body.metadata) if body.metadata else None

    try:
        async with get_session() as session:
            record = RecordEntry(
                record_id=rec_id,
                namespace_id=auth.namespace_id,
                leaf_index=entry.leaf_index,
                leaf_hash=entry.leaf_hash,
                mmr_root=entry.mmr_root,
                chain_depth=entry.leaf_index + 1,
                payload_hash=entry.payload_hash,
                record_type=record_type,
                external_id=external_id,
                record_hash=record_hash,
                metadata_json=metadata_str,
                created_at_exact=entry.created_at,
            )
            session.add(record)
    except Exception as exc:
        logger.warning(f"[Etch] Record persist failed: {exc}")

    logger.info(f"[Etch] Record {rec_id} committed to {auth.namespace_id} chain_pos={entry.leaf_index}")

    metadata = body.metadata if body.metadata else None

    return RecordReceipt(
        id=rec_id,
        record_hash=record_hash,
        leaf_hash=entry.leaf_hash,
        mmr_root=entry.mmr_root,
        chain_position=entry.leaf_index,
        chain_depth=entry.leaf_index + 1,
        timestamp=entry.created_at,
        namespace=auth.namespace_id,
        record_type=record_type,
        external_id=external_id,
        metadata=metadata,
    )


@records_router.get("/v1/records", summary="List and filter records")
async def list_records(
    auth: AuthContext = Depends(require_auth),
    type: Optional[str] = Query(None, description="Filter by record type"),
    external_id: Optional[str] = Query(None, description="Filter by external ID"),
    actor: Optional[str] = Query(None, description="Filter by metadata actor (substring match)"),
    after: Optional[str] = Query(None, description="Cursor: return records after this record_id"),
    before: Optional[str] = Query(None, description="Cursor: return records before this record_id"),
    limit: int = Query(default=50, ge=1, le=500),
) -> RecordListResponse:
    """List records in the authenticated namespace with optional filters."""
    try:
        async with get_session() as session:
            # Base query scoped to namespace
            query = select(RecordEntry).where(RecordEntry.namespace_id == auth.namespace_id)

            if type:
                query = query.where(RecordEntry.record_type == type)
            if external_id:
                query = query.where(RecordEntry.external_id == external_id)
            if actor:
                query = query.where(RecordEntry.metadata_json.contains(actor))

            # Cursor pagination
            if after:
                cursor_result = await session.execute(
                    select(RecordEntry.leaf_index).where(RecordEntry.record_id == after)
                )
                cursor_idx = cursor_result.scalar_one_or_none()
                if cursor_idx is not None:
                    query = query.where(RecordEntry.leaf_index > cursor_idx)

            if before:
                cursor_result = await session.execute(
                    select(RecordEntry.leaf_index).where(RecordEntry.record_id == before)
                )
                cursor_idx = cursor_result.scalar_one_or_none()
                if cursor_idx is not None:
                    query = query.where(RecordEntry.leaf_index < cursor_idx)

            # Count total (without cursor/limit)
            count_query = select(func.count()).select_from(RecordEntry).where(
                RecordEntry.namespace_id == auth.namespace_id
            )
            if type:
                count_query = count_query.where(RecordEntry.record_type == type)
            if external_id:
                count_query = count_query.where(RecordEntry.external_id == external_id)
            count_result = await session.execute(count_query)
            total = count_result.scalar() or 0

            # Fetch one extra to determine has_more
            query = query.order_by(RecordEntry.leaf_index.desc()).limit(limit + 1)
            result = await session.execute(query)
            rows = result.scalars().all()

        has_more = len(rows) > limit
        rows = rows[:limit]

        data = []
        for r in rows:
            metadata = json.loads(r.metadata_json) if r.metadata_json else None
            data.append(RecordReceipt(
                id=r.record_id,
                record_hash=r.record_hash,
                leaf_hash=r.leaf_hash,
                mmr_root=r.mmr_root,
                chain_position=r.leaf_index,
                chain_depth=r.chain_depth,
                timestamp=r.created_at_exact or r.created_at.timestamp(),
                namespace=r.namespace_id,
                record_type=r.record_type,
                external_id=r.external_id,
                metadata=metadata,
            ))

        return RecordListResponse(data=data, has_more=has_more, total=total)

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[Etch] List records failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")


@records_router.get("/v1/records/{record_id}/proof", summary="Get self-contained inclusion proof")
async def get_record_proof(record_id: str, auth: AuthContext = Depends(require_auth)) -> InclusionProofResponse:
    """
    Return a portable, offline-verifiable inclusion proof for a record.
    Any party can verify this proof with 4 lines of code, no Etch SDK required.
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.record_id == record_id,
                    RecordEntry.namespace_id == auth.namespace_id,
                )
            )
            record = result.scalar_one_or_none()

            if not record:
                raise HTTPException(status_code=404, detail=f"Record {record_id} not found")

            # Get previous record to find prev_root
            prev_result = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.namespace_id == auth.namespace_id,
                    RecordEntry.leaf_index == record.leaf_index - 1,
                )
            )
            prev_record = prev_result.scalar_one_or_none()
            prev_root = prev_record.mmr_root if prev_record else "0" * 64

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[Etch] Proof lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    ts = record.created_at_exact or record.created_at.timestamp()

    return InclusionProofResponse(
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


@records_router.get("/v1/records/{record_id}", summary="Retrieve a record receipt")
async def get_record(record_id: str, auth: AuthContext = Depends(require_auth)) -> RecordReceipt:
    """Retrieve a record receipt by its Etch record ID."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.record_id == record_id,
                    RecordEntry.namespace_id == auth.namespace_id,
                )
            )
            record = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[Etch] Record lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not record:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found")

    metadata = json.loads(record.metadata_json) if record.metadata_json else None

    return RecordReceipt(
        id=record.record_id,
        record_hash=record.record_hash,
        leaf_hash=record.leaf_hash,
        mmr_root=record.mmr_root,
        chain_position=record.leaf_index,
        chain_depth=record.chain_depth,
        timestamp=record.created_at_exact or record.created_at.timestamp(),
        namespace=record.namespace_id,
        record_type=record.record_type,
        external_id=record.external_id,
        metadata=metadata,
    )


@records_router.post("/v1/records/verify", summary="Verify record against chain")
async def verify_record(body: VerifyRecordRequest, auth: AuthContext = Depends(require_auth)) -> VerifyRecordResponse:
    """
    Verify that record data matches what was committed, and that the chain
    commitment is cryptographically intact.
    """
    # Resolve the hash to verify against
    if body.record_hash:
        provided_hash = body.record_hash
    elif body.record and body.record.data:
        provided_hash = _hash_record_data(body.record.data)
    else:
        raise HTTPException(status_code=422, detail="Provide either record.data or record_hash")

    try:
        async with get_session() as session:
            # Get the record and its predecessor
            result = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.record_id == body.record_id,
                    RecordEntry.namespace_id == auth.namespace_id,
                )
            )
            record = result.scalar_one_or_none()

            if not record:
                raise HTTPException(status_code=404, detail=f"Record {body.record_id} not found")

            # Get predecessor for chain integrity check
            prev_result = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.namespace_id == auth.namespace_id,
                    RecordEntry.leaf_index == record.leaf_index - 1,
                )
            )
            prev_record = prev_result.scalar_one_or_none()
            prev_root = prev_record.mmr_root if prev_record else "0" * 64

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[Etch] Verify lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Check 1: content hash match
    content_match = record.record_hash == provided_hash

    # Check 2: chain integrity — recompute leaf_hash
    chain_integrity = False
    try:
        ts = record.created_at_exact or record.created_at.timestamp()
        expected_leaf = _sha256(f"{prev_root}:record_commit:{record.payload_hash}:{ts}")
        chain_integrity = expected_leaf == record.leaf_hash
    except Exception as exc:
        logger.warning(f"[Etch] Chain integrity check failed: {exc}")

    return VerifyRecordResponse(
        record_id=body.record_id,
        content_match=content_match,
        chain_integrity=chain_integrity,
        verified=content_match and chain_integrity,
        verified_at=time.time(),
        original_timestamp=record.created_at_exact or record.created_at.timestamp(),
    )


@records_router.get("/v1/chain/root", summary="Current chain state")
async def chain_root(auth: AuthContext = Depends(require_auth)) -> ChainStateResponse:
    """Return the current chain root and depth for the authenticated namespace."""
    manager = get_chain_manager()
    chain = await manager.get_chain(auth.namespace_id)

    return ChainStateResponse(
        mmr_root=chain.current_root(),
        chain_depth=chain.leaf_count(),
        namespace=auth.namespace_id,
        timestamp=time.time(),
    )
