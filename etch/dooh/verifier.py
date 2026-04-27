"""
Offline verifier for SignedBundles.

Runs the six-step check from docs/dooh-spec.md:
  1. Resolve key_ids against the manifest.
  2. Verify both ed25519 sigs over the canonical body.
  3. Re-compute bundle_hash, confirm it matches etch_proof.record_hash.
  4. Verify the Etch inclusion proof's internal consistency.
  5. Confirm etch_proof.mmr_root matches a trusted chain root (optional).
  6. Confirm etch_proof.timestamp >= played_at.

Steps 1-4 and 6 require no network. Step 5 needs a trusted root supplied
by the caller (fetched out-of-band from the advertiser's published feed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from etch.chain import InclusionProof, verify_inclusion_proof

from .keys import b64url_decode, verify_signature
from .manifest import IdentityManifest
from .receipt import SignedBundle

# action_type used by Etch's records_api when committing.
_RECORD_COMMIT_ACTION = "record_commit"


@dataclass
class VerifyResult:
    ok: bool
    failed_steps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def _parse_iso(ts: str) -> float:
    """Parse RFC 3339 to unix timestamp (seconds)."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def verify_bundle(
    bundle: SignedBundle,
    manifest: IdentityManifest,
    trusted_root: Optional[str] = None,
) -> VerifyResult:
    """Run the full offline verification flow.

    Args:
        bundle: the SignedBundle to verify (must include etch_proof).
        manifest: advertiser's identity manifest mapping key_id → public key.
        trusted_root: optional published mmr_root for step 5. If None, step 5
            is skipped and a warning is added to the result.
    """
    failed: List[str] = []
    warnings: List[str] = []

    if bundle.etch_proof is None:
        return VerifyResult(ok=False, failed_steps=["bundle has no etch_proof — never anchored"])

    proof = bundle.etch_proof

    # Step 1 — resolve key_ids
    player_pub = manifest.resolve(bundle.player_sig.key_id)
    venue_pub = manifest.resolve(bundle.venue_countersig.key_id)
    if player_pub is None:
        failed.append(f"unknown player key_id: {bundle.player_sig.key_id}")
    if venue_pub is None:
        failed.append(f"unknown venue key_id: {bundle.venue_countersig.key_id}")
    if failed:
        return VerifyResult(ok=False, failed_steps=failed, warnings=warnings)

    # Step 2 — verify both signatures over canonical body
    body_bytes = bundle.receipt.canonical_bytes()
    if not verify_signature(player_pub, body_bytes, b64url_decode(bundle.player_sig.sig)):
        failed.append("player_sig invalid")
    if not verify_signature(venue_pub, body_bytes, b64url_decode(bundle.venue_countersig.sig)):
        failed.append("venue_countersig invalid")

    # Step 3 — bundle hash matches what was anchored
    recomputed = bundle.bundle_hash()
    if recomputed != proof.record_hash:
        failed.append(f"bundle_hash mismatch: computed {recomputed[:16]}... vs anchored {proof.record_hash[:16]}...")

    # Step 4 — Etch proof internal consistency
    inclusion = InclusionProof(
        leaf_index=proof.leaf_index,
        leaf_hash=proof.leaf_hash,
        mmr_root=proof.mmr_root,
        prev_root=proof.prev_root,
        action_type=_RECORD_COMMIT_ACTION,
        payload_hash=proof.payload_hash,
        timestamp=proof.timestamp,
    )
    if not verify_inclusion_proof(inclusion):
        failed.append("Etch inclusion proof internal consistency failed")

    # Step 5 — trusted-root match (optional)
    if trusted_root is None:
        warnings.append("trusted_root not provided — skipping namespace-root match")
    elif trusted_root != proof.mmr_root:
        # If the trusted root is later than this leaf, the SDK would need a
        # chain-consistency proof to verify; v0 only supports point-equality.
        failed.append(
            f"mmr_root mismatch with trusted_root (point-equality only in v0): "
            f"proof={proof.mmr_root[:16]}... trusted={trusted_root[:16]}..."
        )

    # Step 6 — timestamp ordering
    played_unix = _parse_iso(bundle.receipt.played_at)
    if proof.timestamp + 1.0 < played_unix:
        # 1-second slack for clock jitter between player and Etch server.
        failed.append(
            f"played_at ({bundle.receipt.played_at}) is later than chain anchor "
            f"({proof.timestamp}) — clock claim implausible"
        )

    return VerifyResult(ok=not failed, failed_steps=failed, warnings=warnings)
