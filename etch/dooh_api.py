"""
Etch DOOH API — /v1/dooh/*

Thin convenience layer over /v1/records for digital out-of-home playback
receipts. Stores the full SignedBundle in record metadata so verifiers can
fetch bundles + Etch proofs in one call, filtered by campaign/screen/time.

Endpoints:
    POST /v1/dooh/receipts        - Anchor a bundle, store it, return full bundle + proof
    GET  /v1/dooh/receipts        - Query bundles by campaign/screen/played_at range
    POST /v1/dooh/verify          - Server-side verify of a SignedBundle

See docs/dooh-spec.md for the receipt format and trust model.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from .auth import AuthContext, require_auth
from .chain_manager import get_chain_manager
from .db import get_session
from .dooh.manifest import IdentityManifest
from .dooh.receipt import EtchProof, SignedBundle, attach_proof, bundle_to_dict
from .dooh.verifier import verify_bundle as offline_verify
from .models import RecordEntry, generate_record_id

logger = logging.getLogger(__name__)

dooh_router = APIRouter(tags=["Etch DOOH"])

DOOH_RECORD_TYPE = "dooh-receipt"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateReceiptRequest(BaseModel):
    bundle: SignedBundle = Field(description="Signed bundle without etch_proof")


class CreateReceiptResponse(BaseModel):
    bundle: SignedBundle = Field(description="Bundle with etch_proof attached")


class ListReceiptsResponse(BaseModel):
    data: List[SignedBundle]
    has_more: bool
    total: int


class VerifyRequest(BaseModel):
    bundle: SignedBundle
    manifest: IdentityManifest
    trusted_root: Optional[str] = None


class VerifyResponse(BaseModel):
    ok: bool
    failed_steps: List[str]
    warnings: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso_to_unix(ts: str) -> float:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _row_to_bundle(row: RecordEntry) -> Optional[SignedBundle]:
    """Reconstruct a SignedBundle from the metadata blob we stored at submit time."""
    if not row.metadata_json:
        return None
    try:
        meta = json.loads(row.metadata_json)
    except json.JSONDecodeError:
        return None
    bundle_dict = meta.get("bundle")
    if not bundle_dict:
        return None
    try:
        return SignedBundle.model_validate(bundle_dict)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@dooh_router.post("/v1/dooh/receipts", summary="Anchor a DOOH playback receipt")
async def create_receipt(
    body: CreateReceiptRequest,
    auth: AuthContext = Depends(require_auth),
) -> CreateReceiptResponse:
    """
    Anchor a SignedBundle in the namespace chain. Stores the full bundle in
    record metadata so it can be retrieved via GET /v1/dooh/receipts.

    The advertiser's namespace owns this chain. The bundle's player_sig and
    venue_countersig are NOT validated server-side here — verification happens
    via /v1/dooh/verify or any offline verifier. This endpoint is just an
    anchor + storage convenience.
    """
    if body.bundle.etch_proof is not None:
        raise HTTPException(
            status_code=422,
            detail="Submit bundles without etch_proof; the server attaches it.",
        )

    bundle_hash = body.bundle.bundle_hash()

    # Anchor via the namespace chain (same pattern as records_api.create_record)
    chain_payload = {
        "record_hash": bundle_hash,
        "record_type": DOOH_RECORD_TYPE,
        "external_id": body.bundle.receipt.campaign_id,
        "namespace": auth.namespace_id,
        "registered_at": time.time(),
    }
    manager = get_chain_manager()
    chain = await manager.get_chain(auth.namespace_id)
    entry = chain.append(
        action_type="record_commit",
        payload=chain_payload,
        specialist="etch-dooh",
        agent_id=bundle_hash,
    )

    rec_id = generate_record_id()
    bundle_dict = bundle_to_dict(body.bundle)
    metadata = {
        "kind": DOOH_RECORD_TYPE,
        "bundle": bundle_dict,
        "screen_id": body.bundle.receipt.screen_id,
        "venue_id": body.bundle.receipt.venue_id,
        "sequence": body.bundle.receipt.sequence,
        "played_at": body.bundle.receipt.played_at,
        "played_at_unix": _parse_iso_to_unix(body.bundle.receipt.played_at),
    }

    # Find prev_root so we can return a complete EtchProof to the caller.
    prev_root = "0" * 64
    if entry.leaf_index > 0:
        async with get_session() as session:
            prev = await session.execute(
                select(RecordEntry).where(
                    RecordEntry.namespace_id == auth.namespace_id,
                    RecordEntry.leaf_index == entry.leaf_index - 1,
                )
            )
            prev_row = prev.scalar_one_or_none()
            if prev_row:
                prev_root = prev_row.mmr_root

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
                record_type=DOOH_RECORD_TYPE,
                external_id=body.bundle.receipt.campaign_id,
                record_hash=bundle_hash,
                metadata_json=json.dumps(metadata),
                created_at_exact=entry.created_at,
            )
            session.add(record)
    except Exception as exc:
        logger.warning(f"[Etch DOOH] persist failed: {exc}")
        raise HTTPException(status_code=503, detail="Persist failed")

    proof = EtchProof(
        namespace=auth.namespace_id,
        record_id=rec_id,
        leaf_index=entry.leaf_index,
        leaf_hash=entry.leaf_hash,
        mmr_root=entry.mmr_root,
        prev_root=prev_root,
        payload_hash=entry.payload_hash,
        timestamp=entry.created_at,
        record_hash=bundle_hash,
    )
    return CreateReceiptResponse(bundle=attach_proof(body.bundle, proof))


@dooh_router.get("/v1/dooh/receipts", summary="Query DOOH playback receipts")
async def list_receipts(
    auth: AuthContext = Depends(require_auth),
    campaign_id: Optional[str] = Query(None, description="Filter by campaign_id"),
    screen_id: Optional[str] = Query(None, description="Filter by screen_id"),
    played_at_from: Optional[str] = Query(None, description="RFC 3339 lower bound on played_at (inclusive)"),
    played_at_to: Optional[str] = Query(None, description="RFC 3339 upper bound on played_at (inclusive)"),
    after: Optional[str] = Query(None, description="Cursor: return records after this record_id"),
    limit: int = Query(default=50, ge=1, le=500),
) -> ListReceiptsResponse:
    """
    Return matching SignedBundles (with etch_proof attached) ordered by chain
    position descending. `played_at_from`/`played_at_to` filter on the embedded
    receipt's `played_at`, not the chain timestamp.
    """
    from_unix = _parse_iso_to_unix(played_at_from) if played_at_from else None
    to_unix = _parse_iso_to_unix(played_at_to) if played_at_to else None

    async with get_session() as session:
        query = (
            select(RecordEntry)
            .where(RecordEntry.namespace_id == auth.namespace_id)
            .where(RecordEntry.record_type == DOOH_RECORD_TYPE)
        )
        if campaign_id:
            query = query.where(RecordEntry.external_id == campaign_id)
        if after:
            cursor = await session.execute(
                select(RecordEntry.leaf_index).where(RecordEntry.record_id == after)
            )
            cursor_idx = cursor.scalar_one_or_none()
            if cursor_idx is not None:
                query = query.where(RecordEntry.leaf_index < cursor_idx)
        # Fetch one extra to know has_more; filter played_at + screen_id in Python.
        rows_result = await session.execute(query.order_by(RecordEntry.leaf_index.desc()))
        rows = rows_result.scalars().all()

    bundles: List[SignedBundle] = []
    for r in rows:
        bundle = _row_to_bundle(r)
        if bundle is None:
            continue
        if screen_id and bundle.receipt.screen_id != screen_id:
            continue
        if from_unix is not None or to_unix is not None:
            played_unix = _parse_iso_to_unix(bundle.receipt.played_at)
            if from_unix is not None and played_unix < from_unix:
                continue
            if to_unix is not None and played_unix > to_unix:
                continue

        # Reconstruct etch_proof from the row (the stored bundle was the
        # pre-anchor version; we attach the proof from chain state).
        prev_root = "0" * 64
        if r.leaf_index > 0:
            async with get_session() as session:
                prev = await session.execute(
                    select(RecordEntry).where(
                        RecordEntry.namespace_id == auth.namespace_id,
                        RecordEntry.leaf_index == r.leaf_index - 1,
                    )
                )
                prev_row = prev.scalar_one_or_none()
                if prev_row:
                    prev_root = prev_row.mmr_root

        proof = EtchProof(
            namespace=r.namespace_id,
            record_id=r.record_id,
            leaf_index=r.leaf_index,
            leaf_hash=r.leaf_hash,
            mmr_root=r.mmr_root,
            prev_root=prev_root,
            payload_hash=r.payload_hash,
            timestamp=r.created_at_exact or r.created_at.timestamp(),
            record_hash=r.record_hash,
        )
        bundles.append(attach_proof(bundle, proof))
        if len(bundles) >= limit + 1:
            break

    has_more = len(bundles) > limit
    bundles = bundles[:limit]
    return ListReceiptsResponse(data=bundles, has_more=has_more, total=len(bundles))


@dooh_router.post("/v1/dooh/verify", summary="Server-side verify a SignedBundle")
async def verify_receipt(
    body: VerifyRequest,
    auth: AuthContext = Depends(require_auth),
) -> VerifyResponse:
    """
    Run the same offline verification flow the SDK runs, server-side. Useful
    for callers who don't want to install the Python SDK. The server does NOT
    look up the chain root; pass `trusted_root` if you want the namespace-root
    match to be checked.

    The auth context is used only to scope this endpoint to a paying caller —
    it has no effect on the verification logic.
    """
    _ = auth  # scope only
    result = offline_verify(body.bundle, body.manifest, trusted_root=body.trusted_root)
    return VerifyResponse(
        ok=result.ok,
        failed_steps=result.failed_steps,
        warnings=result.warnings,
    )


def _record_to_dict(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Tiny helper kept for symmetry with records_api in case we extend later."""
    return metadata
