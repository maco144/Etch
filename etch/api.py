"""
Etch — Content Provenance API

Timestamps content on a tamper-evident Merkle chain, creating proof receipts
that prove content existed at a specific point in time.

Endpoints:
    POST /v1/proof                      - Register content -> get proof receipt
    GET  /v1/proof/recent               - List recent proofs (paginated)
    GET  /v1/proof/stats                - Chain statistics
    GET  /v1/proof/{proof_id}           - Retrieve receipt by proof_id (leaf_index)
    GET  /v1/proof/hash/{content_hash}  - Look up proof by content SHA-256
    POST /v1/proof/{proof_id}/verify    - Verify content matches a stored proof

Design:
  - Content is NEVER stored — only SHA-256(content) is logged (privacy-preserving).
  - proof_id is the MMR leaf_index (integer, monotonically increasing, globally unique).
  - Chain integrity can be independently verified by any party using the inclusion proof.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_

from .chain import get_chain, log_event
from .db import get_session
from .models import ProofRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/proof", tags=["Etch"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ProofRequest(BaseModel):
    content: Optional[str] = Field(
        None,
        description="Raw content to hash and register. Mutually exclusive with content_hash.",
    )
    content_hash: Optional[str] = Field(
        None,
        description="Pre-computed SHA-256 hex of the content (64 chars). Use when you want "
                    "to register without transmitting the raw content.",
    )
    label: Optional[str] = Field(
        None,
        max_length=200,
        description="Human-readable label for this proof (e.g. 'Article: My Post Title').",
    )
    owner: Optional[str] = Field(
        None,
        max_length=200,
        description="Owner identifier (e.g. user ID, wallet address, email hash).",
    )


class ProofReceipt(BaseModel):
    proof_id: int = Field(description="MMR leaf index — unique, monotonically increasing")
    content_hash: str = Field(description="SHA-256 hex of the registered content")
    label: Optional[str]
    owner: Optional[str]
    timestamp: float = Field(description="Unix timestamp (seconds, UTC)")
    leaf_hash: str = Field(description="SHA-256 commitment: hash(prev_root:action:payload_hash:ts)")
    mmr_root: str = Field(description="MMR root after this leaf was appended")
    chain_depth: int = Field(description="Total leaves in the chain at registration time")


class VerifyRequest(BaseModel):
    content: Optional[str] = Field(
        None,
        description="Raw content to verify against the stored proof.",
    )
    content_hash: Optional[str] = Field(
        None,
        description="Pre-computed SHA-256 hex to verify.",
    )


class VerifyResponse(BaseModel):
    proof_id: int
    content_hash_matches: bool
    chain_integrity_valid: bool
    verified: bool = Field(description="True only when both content_hash and chain integrity pass")
    receipt: ProofReceipt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


async def _persist_to_db(entry, content_hash: str, label: Optional[str], owner: Optional[str]):
    """Persist a chain entry to the database."""
    try:
        async with get_session() as session:
            record = ProofRecord(
                leaf_index=entry.leaf_index,
                leaf_hash=entry.leaf_hash,
                mmr_root=entry.mmr_root,
                leaf_count=entry.leaf_index + 1,
                payload_hash=entry.payload_hash,
                action_type="content_proof",
                content_hash=content_hash,
                label=label,
                owner=owner,
                proof_json=json.dumps({
                    "content_hash": content_hash,
                    "label": label or "",
                    "owner": owner or "",
                }),
                created_at_exact=entry.created_at,
            )
            session.add(record)
    except Exception as exc:
        logger.warning(f"[Etch] DB persist failed: {exc}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", summary="Register content and get a tamper-evident proof receipt")
async def register_proof(body: ProofRequest) -> ProofReceipt:
    """
    Submit content (or its SHA-256 hash) to be timestamped on the Merkle chain.

    The raw content is never stored — only its SHA-256 hash. Returns a receipt with
    the proof_id, cryptographic commitments, and timestamp.
    """
    if not body.content and not body.content_hash:
        raise HTTPException(status_code=422, detail="Provide either 'content' or 'content_hash'")

    if body.content_hash and len(body.content_hash) != 64:
        raise HTTPException(status_code=422, detail="content_hash must be a 64-character SHA-256 hex string")

    content_hash = body.content_hash or _sha256(body.content)

    payload = {
        "content_hash": content_hash,
        "label": body.label or "",
        "owner": body.owner or "",
        "registered_at": time.time(),
    }

    entry = log_event(
        action_type="content_proof",
        payload=payload,
        specialist="etch",
        agent_id=content_hash,
    )

    await _persist_to_db(entry, content_hash, body.label, body.owner)

    logger.info(f"[Etch] Registered proof_id={entry.leaf_index} hash={content_hash[:12]}...")

    return ProofReceipt(
        proof_id=entry.leaf_index,
        content_hash=content_hash,
        label=body.label,
        owner=body.owner,
        timestamp=entry.created_at,
        leaf_hash=entry.leaf_hash,
        mmr_root=entry.mmr_root,
        chain_depth=entry.leaf_index + 1,
    )


@router.get("/recent", summary="List recent content proofs")
async def list_recent_proofs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List recent content proofs, newest first."""
    try:
        async with get_session() as session:
            count_result = await session.execute(
                select(func.count()).select_from(ProofRecord)
            )
            total = count_result.scalar() or 0

            result = await session.execute(
                select(ProofRecord)
                .order_by(ProofRecord.leaf_index.desc())
                .offset(offset)
                .limit(limit)
            )
            records = result.scalars().all()

        proofs = []
        for r in records:
            ts = r.created_at.timestamp() if hasattr(r.created_at, "timestamp") else r.created_at
            proofs.append({
                "proof_id": r.leaf_index,
                "content_hash": r.content_hash,
                "label": r.label,
                "owner": r.owner,
                "timestamp": ts,
                "leaf_hash": r.leaf_hash,
                "mmr_root": r.mmr_root,
            })

        return {"proofs": proofs, "total": total, "limit": limit, "offset": offset}
    except Exception as exc:
        logger.warning(f"[Etch] List failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")


@router.get("/stats", summary="Etch chain statistics")
async def proof_stats() -> dict:
    """Return basic statistics about the Etch chain."""
    chain = get_chain()

    try:
        async with get_session() as session:
            result = await session.execute(
                select(
                    func.count().label("total_proofs"),
                    func.min(ProofRecord.created_at).label("first_proof"),
                    func.max(ProofRecord.created_at).label("last_proof"),
                )
            )
            row = result.one()

        first_ts = None
        last_ts = None
        if row.first_proof:
            first_ts = row.first_proof.timestamp() if hasattr(row.first_proof, "timestamp") else row.first_proof
        if row.last_proof:
            last_ts = row.last_proof.timestamp() if hasattr(row.last_proof, "timestamp") else row.last_proof

        return {
            "total_proofs": row.total_proofs or 0,
            "chain_depth": chain.leaf_count(),
            "mmr_root": chain.current_root(),
            "first_proof_at": first_ts,
            "last_proof_at": last_ts,
        }
    except Exception as exc:
        logger.warning(f"[Etch] Stats failed: {exc}")
        return {
            "total_proofs": 0,
            "chain_depth": chain.leaf_count(),
            "mmr_root": chain.current_root(),
            "first_proof_at": None,
            "last_proof_at": None,
        }


@router.get("/hash/{content_hash}", summary="Look up a proof by content SHA-256")
async def get_proof_by_hash(content_hash: str) -> ProofReceipt:
    """Retrieve a proof receipt using the SHA-256 hash of the content."""
    if len(content_hash) != 64:
        raise HTTPException(status_code=422, detail="content_hash must be 64 hex characters")

    try:
        async with get_session() as session:
            result = await session.execute(
                select(ProofRecord)
                .where(ProofRecord.content_hash == content_hash)
                .order_by(ProofRecord.leaf_index.asc())
                .limit(1)
            )
            record = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[Etch] DB lookup failed: {exc}")
        record = None

    if not record:
        raise HTTPException(status_code=404, detail=f"No proof found for content_hash={content_hash[:12]}...")

    return ProofReceipt(
        proof_id=record.leaf_index,
        content_hash=record.content_hash,
        label=record.label,
        owner=record.owner,
        timestamp=record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else record.created_at,
        leaf_hash=record.leaf_hash,
        mmr_root=record.mmr_root,
        chain_depth=record.leaf_count,
    )


@router.get("/{proof_id}", summary="Retrieve a proof receipt by proof_id")
async def get_proof(proof_id: int) -> ProofReceipt:
    """Retrieve a previously registered proof receipt by its proof_id."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ProofRecord)
                .where(ProofRecord.leaf_index == proof_id)
            )
            record = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[Etch] DB lookup failed: {exc}")
        record = None

    if not record:
        raise HTTPException(status_code=404, detail=f"Proof {proof_id} not found")

    return ProofReceipt(
        proof_id=record.leaf_index,
        content_hash=record.content_hash,
        label=record.label,
        owner=record.owner,
        timestamp=record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else record.created_at,
        leaf_hash=record.leaf_hash,
        mmr_root=record.mmr_root,
        chain_depth=record.leaf_count,
    )


@router.post("/{proof_id}/verify", summary="Verify content matches a stored proof")
async def verify_proof(proof_id: int, body: VerifyRequest) -> VerifyResponse:
    """
    Verify that content matches the proof stored at proof_id, and that the proof
    itself is cryptographically intact on the chain.
    """
    if not body.content and not body.content_hash:
        raise HTTPException(status_code=422, detail="Provide either 'content' or 'content_hash'")

    provided_hash = body.content_hash or _sha256(body.content)

    try:
        async with get_session() as session:
            result = await session.execute(
                select(ProofRecord).where(
                    or_(
                        ProofRecord.leaf_index == proof_id,
                        ProofRecord.leaf_index == proof_id - 1,
                    )
                ).order_by(ProofRecord.leaf_index.asc())
            )
            records = result.scalars().all()
    except Exception as exc:
        logger.warning(f"[Etch] DB lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    record = next((r for r in records if r.leaf_index == proof_id), None)
    if not record:
        raise HTTPException(status_code=404, detail=f"Proof {proof_id} not found")

    prev_record = next((r for r in records if r.leaf_index == proof_id - 1), None)

    # Check 1: content hash match
    content_hash_matches = record.content_hash == provided_hash

    # Check 2: chain integrity — recompute leaf_hash from stored fields
    chain_integrity_valid = False
    try:
        prev_root = prev_record.mmr_root if prev_record else "0" * 64
        ts = record.created_at_exact if record.created_at_exact is not None else (
            record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else record.created_at
        )
        expected_leaf_hash = _sha256(f"{prev_root}:{record.action_type}:{record.payload_hash}:{ts}")
        chain_integrity_valid = expected_leaf_hash == record.leaf_hash
    except Exception as exc:
        logger.warning(f"[Etch] Chain integrity check failed: {exc}")

    ts_float = record.created_at_exact if record.created_at_exact is not None else (
        record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else float(record.created_at)
    )
    receipt = ProofReceipt(
        proof_id=record.leaf_index,
        content_hash=record.content_hash,
        label=record.label,
        owner=record.owner,
        timestamp=ts_float,
        leaf_hash=record.leaf_hash,
        mmr_root=record.mmr_root,
        chain_depth=record.leaf_count,
    )

    return VerifyResponse(
        proof_id=proof_id,
        content_hash_matches=content_hash_matches,
        chain_integrity_valid=chain_integrity_valid,
        verified=content_hash_matches and chain_integrity_valid,
        receipt=receipt,
    )
