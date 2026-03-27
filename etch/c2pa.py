"""
Etch C2PA Compatibility Layer

Bridges C2PA (Coalition for Content Provenance and Authenticity) manifests
to the Etch tamper-evident chain. Provides dual-layer provenance:
  - C2PA manifest structure (industry standard, PKI-based)
  - Etch inclusion proof (hash chain, offline-verifiable, no PKI required)

Together they satisfy EU AI Act Article 50 requirements for "robust" and
"independently verifiable" content provenance.

Endpoints:
    POST /v1/c2pa/manifest           - Register a C2PA manifest on the chain
    GET  /v1/c2pa/manifest/{claim_id} - Retrieve manifest + Etch proof
    POST /v1/c2pa/verify             - Verify manifest integrity + chain
    POST /v1/c2pa/bridge             - Bridge existing Etch proof to C2PA format

Design:
  - Manifest data is hashed deterministically (SHA-256, sorted keys).
  - Raw manifest JSON is stored in the DB for retrieval and re-verification.
  - The chain action_type is "c2pa_manifest" to distinguish from content proofs.
  - No C2PA SDK dependency — Etch models the manifest structure natively.
    External PKI signature verification can be added later.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .chain import get_chain, log_event
from .db import get_session
from .models import ProofRecord

logger = logging.getLogger(__name__)

c2pa_router = APIRouter(prefix="/v1/c2pa", tags=["C2PA"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class C2PAAssertion(BaseModel):
    """Single C2PA assertion — a structured claim about content."""
    label: str = Field(
        description="Assertion type label (e.g. 'c2pa.created', 'c2pa.actions', 'c2pa.hash.data')"
    )
    data: Dict[str, Any] = Field(
        description="Assertion payload — structure depends on the label"
    )


class C2PAClaim(BaseModel):
    """C2PA claim — a set of assertions about content, made by a claim generator."""
    claim_generator: str = Field(
        description="Who generated this claim (e.g. 'Adobe Photoshop 25.0', 'Claude API')"
    )
    claim_generator_info: Optional[Dict[str, Any]] = Field(
        None, description="Additional info about the generator (version, build, etc.)"
    )
    assertions: List[C2PAAssertion] = Field(
        min_length=1, description="One or more assertions about the content"
    )
    signature: Optional[str] = Field(
        None, description="Optional external PKI signature over the claim (hex-encoded)"
    )


class C2PAIngredient(BaseModel):
    """Reference to a prior version or source asset."""
    title: Optional[str] = Field(None, description="Human-readable title of the ingredient")
    content_hash: str = Field(description="SHA-256 of the ingredient content")
    relationship: str = Field(
        default="parentOf",
        description="Relationship type: 'parentOf', 'componentOf', 'inputTo'"
    )
    etch_proof_id: Optional[int] = Field(
        None, description="If the ingredient was registered on Etch, its proof_id"
    )


class C2PAManifestRequest(BaseModel):
    """Register a C2PA manifest on the Etch chain."""
    content_hash: str = Field(description="SHA-256 of the content this manifest describes (64 hex chars)")
    title: Optional[str] = Field(None, max_length=500, description="Human-readable content title")
    format: Optional[str] = Field(None, max_length=100, description="MIME type (e.g. 'image/png', 'application/pdf')")
    claim: C2PAClaim = Field(description="The C2PA claim with assertions")
    ingredients: Optional[List[C2PAIngredient]] = Field(
        None, description="Prior versions or source assets that fed into this content"
    )


class C2PAManifestReceipt(BaseModel):
    """Response after registering a C2PA manifest."""
    claim_id: int = Field(description="Etch proof_id — use this to retrieve/verify the manifest")
    content_hash: str
    manifest_hash: str = Field(description="SHA-256 of the deterministically serialized manifest")
    title: Optional[str]
    claim_generator: str
    assertion_count: int
    ingredient_count: int
    timestamp: float
    leaf_hash: str
    mmr_root: str
    chain_depth: int
    # Embedded inclusion proof for offline verification
    inclusion_proof: Dict[str, Any] = Field(description="Self-contained proof for offline verification")


class C2PAVerifyRequest(BaseModel):
    """Verify a C2PA manifest against the Etch chain."""
    claim_id: int = Field(description="The claim_id (proof_id) returned at registration")
    content_hash: Optional[str] = Field(
        None, description="SHA-256 of the content to verify against"
    )
    manifest: Optional[C2PAManifestRequest] = Field(
        None, description="Full manifest to verify (checks manifest_hash match)"
    )


class C2PAVerifyResponse(BaseModel):
    """Verification result for a C2PA manifest."""
    claim_id: int
    content_hash_match: bool = Field(description="True if provided content_hash matches registered")
    manifest_hash_match: bool = Field(description="True if provided manifest hashes to the same value")
    chain_integrity: bool = Field(description="True if the chain entry is cryptographically intact")
    verified: bool = Field(description="True only when all applicable checks pass")
    timestamp: float = Field(description="Original registration timestamp")
    mmr_root: str
    details: Dict[str, Any] = Field(default_factory=dict)


class C2PABridgeRequest(BaseModel):
    """Bridge an existing Etch proof into C2PA manifest format."""
    proof_id: int = Field(description="Existing Etch proof_id to bridge")
    claim_generator: str = Field(description="Claim generator identifier")
    assertions: Optional[List[C2PAAssertion]] = Field(
        None, description="Additional assertions to attach (optional)"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _manifest_hash(manifest: C2PAManifestRequest) -> str:
    """Deterministic hash of a C2PA manifest."""
    canonical = json.dumps(manifest.model_dump(), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@c2pa_router.post("/manifest", summary="Register a C2PA manifest on the Etch chain")
async def register_manifest(body: C2PAManifestRequest) -> C2PAManifestReceipt:
    """
    Register a C2PA-structured manifest on the Etch tamper-evident chain.

    The manifest is hashed deterministically and committed to the Merkle chain.
    Returns a receipt with an embedded inclusion proof that can be verified offline
    by any party — no Etch server or C2PA PKI required.

    This provides dual-layer provenance: C2PA structure + Etch chain integrity.
    """
    if len(body.content_hash) != 64:
        raise HTTPException(status_code=422, detail="content_hash must be a 64-character SHA-256 hex string")

    manifest_h = _manifest_hash(body)
    ingredient_count = len(body.ingredients) if body.ingredients else 0

    # Build chain payload
    payload = {
        "manifest_hash": manifest_h,
        "content_hash": body.content_hash,
        "claim_generator": body.claim.claim_generator,
        "assertion_count": len(body.claim.assertions),
        "assertion_labels": [a.label for a in body.claim.assertions],
        "ingredient_count": ingredient_count,
        "title": body.title or "",
        "format": body.format or "",
        "registered_at": time.time(),
    }

    # Append to chain
    entry = log_event(
        action_type="c2pa_manifest",
        payload=payload,
        specialist="etch-c2pa",
        agent_id=manifest_h,
    )

    # Persist to DB — store full manifest JSON for retrieval
    manifest_json = json.dumps(body.model_dump(), sort_keys=True, default=str)
    try:
        async with get_session() as session:
            record = ProofRecord(
                leaf_index=entry.leaf_index,
                leaf_hash=entry.leaf_hash,
                mmr_root=entry.mmr_root,
                leaf_count=entry.leaf_index + 1,
                payload_hash=entry.payload_hash,
                action_type="c2pa_manifest",
                content_hash=body.content_hash,
                label=f"c2pa:{body.claim.claim_generator}",
                owner=body.title,
                proof_json=manifest_json,
                created_at_exact=entry.created_at,
            )
            session.add(record)
    except Exception as exc:
        logger.warning(f"[C2PA] DB persist failed: {exc}")

    # Generate inclusion proof
    chain = get_chain()
    proof = chain.generate_proof(entry.leaf_index)
    proof_dict = proof.to_dict() if proof else {}

    logger.info(f"[C2PA] Manifest registered claim_id={entry.leaf_index} generator={body.claim.claim_generator}")

    return C2PAManifestReceipt(
        claim_id=entry.leaf_index,
        content_hash=body.content_hash,
        manifest_hash=manifest_h,
        title=body.title,
        claim_generator=body.claim.claim_generator,
        assertion_count=len(body.claim.assertions),
        ingredient_count=ingredient_count,
        timestamp=entry.created_at,
        leaf_hash=entry.leaf_hash,
        mmr_root=entry.mmr_root,
        chain_depth=entry.leaf_index + 1,
        inclusion_proof=proof_dict,
    )


@c2pa_router.get("/manifest/{claim_id}", summary="Retrieve a C2PA manifest with Etch proof")
async def get_manifest(claim_id: int) -> dict:
    """
    Retrieve a registered C2PA manifest and its current inclusion proof.

    Returns the full manifest structure plus chain commitments for
    offline verification.
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ProofRecord).where(
                    ProofRecord.leaf_index == claim_id,
                    ProofRecord.action_type == "c2pa_manifest",
                )
            )
            record = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[C2PA] DB lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not record:
        raise HTTPException(status_code=404, detail=f"C2PA manifest claim_id={claim_id} not found")

    # Parse stored manifest
    manifest = json.loads(record.proof_json) if record.proof_json else {}

    # Generate fresh inclusion proof
    chain = get_chain()
    proof = chain.generate_proof(record.leaf_index)
    proof_dict = proof.to_dict() if proof else {}

    ts = record.created_at_exact if record.created_at_exact is not None else (
        record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else record.created_at
    )

    return {
        "claim_id": record.leaf_index,
        "content_hash": record.content_hash,
        "manifest": manifest,
        "chain": {
            "leaf_hash": record.leaf_hash,
            "mmr_root": record.mmr_root,
            "chain_depth": record.leaf_count,
            "timestamp": ts,
        },
        "inclusion_proof": proof_dict,
        "verification_steps": [
            "1. Recompute manifest_hash = SHA256(canonical JSON of manifest)",
            "2. Verify leaf_hash = SHA256(prev_root + ':c2pa_manifest:' + payload_hash + ':' + timestamp)",
            "3. Verify mmr_root = SHA256(prev_root + ':' + leaf_hash)",
            "4. If content_hash provided, verify it matches manifest.content_hash",
        ],
    }


@c2pa_router.post("/verify", summary="Verify a C2PA manifest against the Etch chain")
async def verify_manifest(body: C2PAVerifyRequest) -> C2PAVerifyResponse:
    """
    Verify that a C2PA manifest is intact and was registered on the Etch chain.

    Performs up to three checks:
    1. **Chain integrity** — recomputes the leaf_hash from stored fields
    2. **Content hash match** — if content_hash provided, checks it matches
    3. **Manifest hash match** — if full manifest provided, checks deterministic hash

    All checks can be performed offline with the inclusion proof.
    """
    try:
        async with get_session() as session:
            # Fetch the record and its predecessor
            result = await session.execute(
                select(ProofRecord).where(
                    ProofRecord.leaf_index.in_([body.claim_id, body.claim_id - 1])
                ).order_by(ProofRecord.leaf_index.asc())
            )
            records = result.scalars().all()
    except Exception as exc:
        logger.warning(f"[C2PA] DB lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    record = next((r for r in records if r.leaf_index == body.claim_id), None)
    if not record:
        raise HTTPException(status_code=404, detail=f"C2PA manifest claim_id={body.claim_id} not found")

    prev_record = next((r for r in records if r.leaf_index == body.claim_id - 1), None)
    prev_root = prev_record.mmr_root if prev_record else "0" * 64

    ts = record.created_at_exact if record.created_at_exact is not None else (
        record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else record.created_at
    )

    # Check 1: chain integrity
    chain_integrity = False
    try:
        expected_leaf = _sha256(f"{prev_root}:{record.action_type}:{record.payload_hash}:{ts}")
        chain_integrity = expected_leaf == record.leaf_hash
    except Exception as exc:
        logger.warning(f"[C2PA] Chain integrity check failed: {exc}")

    # Check 2: content hash match (optional)
    content_hash_match = True
    if body.content_hash:
        content_hash_match = record.content_hash == body.content_hash

    # Check 3: manifest hash match (optional)
    manifest_hash_match = True
    details = {}
    if body.manifest:
        provided_hash = _manifest_hash(body.manifest)
        # The stored manifest's hash should match
        stored_manifest = json.loads(record.proof_json) if record.proof_json else {}
        stored_hash = hashlib.sha256(
            json.dumps(stored_manifest, sort_keys=True, default=str).encode()
        ).hexdigest()
        manifest_hash_match = provided_hash == stored_hash
        details["provided_manifest_hash"] = provided_hash
        details["stored_manifest_hash"] = stored_hash

    verified = chain_integrity and content_hash_match and manifest_hash_match

    return C2PAVerifyResponse(
        claim_id=body.claim_id,
        content_hash_match=content_hash_match,
        manifest_hash_match=manifest_hash_match,
        chain_integrity=chain_integrity,
        verified=verified,
        timestamp=ts,
        mmr_root=record.mmr_root,
        details=details,
    )


@c2pa_router.post("/bridge", summary="Bridge an existing Etch proof to C2PA format")
async def bridge_to_c2pa(body: C2PABridgeRequest) -> dict:
    """
    Take an existing Etch content proof and wrap it in C2PA manifest structure.

    This allows content that was already registered on Etch to gain C2PA
    compatibility without re-registration. The original proof remains intact;
    a new C2PA manifest entry is created that references it.
    """
    # Look up the original proof
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ProofRecord).where(ProofRecord.leaf_index == body.proof_id)
            )
            original = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[C2PA] Bridge lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not original:
        raise HTTPException(status_code=404, detail=f"Proof {body.proof_id} not found")

    original_ts = original.created_at_exact if original.created_at_exact is not None else (
        original.created_at.timestamp() if hasattr(original.created_at, "timestamp") else original.created_at
    )

    # Build C2PA assertions from the original proof
    assertions = [
        C2PAAssertion(
            label="c2pa.hash.data",
            data={"hash": original.content_hash, "algorithm": "sha256"},
        ),
        C2PAAssertion(
            label="etch.provenance",
            data={
                "original_proof_id": body.proof_id,
                "original_timestamp": original_ts,
                "original_leaf_hash": original.leaf_hash,
                "original_mmr_root": original.mmr_root,
            },
        ),
    ]

    # Add any user-provided assertions
    if body.assertions:
        assertions.extend(body.assertions)

    # Create a C2PA manifest and register it
    manifest = C2PAManifestRequest(
        content_hash=original.content_hash,
        title=original.label,
        claim=C2PAClaim(
            claim_generator=body.claim_generator,
            assertions=assertions,
        ),
        ingredients=[C2PAIngredient(
            title=original.label or f"Etch proof #{body.proof_id}",
            content_hash=original.content_hash,
            relationship="parentOf",
            etch_proof_id=body.proof_id,
        )],
    )

    # Register via the manifest endpoint logic
    receipt = await register_manifest(manifest)

    return {
        "bridged": True,
        "original_proof_id": body.proof_id,
        "c2pa_claim_id": receipt.claim_id,
        "manifest_hash": receipt.manifest_hash,
        "content_hash": original.content_hash,
        "timestamp": receipt.timestamp,
        "mmr_root": receipt.mmr_root,
        "message": f"Etch proof #{body.proof_id} bridged to C2PA manifest (claim_id={receipt.claim_id})",
    }
