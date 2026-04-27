"""
Receipt body, signing, and signed bundle wrapping.

A Receipt is the canonical body that gets signed. A SignedBundle wraps the
body with player_sig, venue_countersig, and (optionally) the Etch inclusion
proof. See docs/dooh-spec.md for field rules.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from .canonical import canonicalize
from .keys import KeyPair, b64url_decode, b64url_encode

RECEIPT_SCHEMA_VERSION = "dooh-receipt/1"
BUNDLE_SCHEMA_VERSION = "dooh-receipt-bundle/1"


class Geo(BaseModel):
    lat: float
    lon: float


class Receipt(BaseModel):
    """The canonical receipt body. Signed by player and venue."""

    advertiser_id: str
    campaign_id: str
    creative_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)
    played_at: str  # RFC 3339 UTC, ms precision
    schema_version: str = RECEIPT_SCHEMA_VERSION
    screen_id: str
    sequence: int = Field(ge=0)
    venue_id: str
    geo: Optional[Geo] = None

    def canonical_bytes(self) -> bytes:
        """Canonical JSON bytes used as the message body for both signatures."""
        # exclude geo if None so optional-omission rule from spec is honored
        data = self.model_dump(exclude_none=True)
        return canonicalize(data)


class Signature(BaseModel):
    alg: str = "ed25519"
    key_id: str
    sig: str  # base64url


class EtchProof(BaseModel):
    """The inclusion proof anchoring this bundle in an Etch namespace chain."""

    namespace: str
    record_id: str
    leaf_index: int
    leaf_hash: str
    mmr_root: str
    prev_root: str
    payload_hash: str
    timestamp: float
    record_hash: str  # = bundle_hash; cross-checked by verifier


class SignedBundle(BaseModel):
    """Body + both signatures + (optional) Etch proof. Persisted on disk."""

    schema_version: str = BUNDLE_SCHEMA_VERSION
    receipt: Receipt
    player_sig: Signature
    venue_countersig: Signature
    etch_proof: Optional[EtchProof] = None

    def bundle_hash(self) -> str:
        """SHA-256 hex over (canonical body || player_sig || venue_countersig).

        This is what gets anchored in Etch as `record_hash`.
        Excludes etch_proof so the hash is stable before/after anchoring.
        """
        digest = hashlib.sha256()
        digest.update(self.receipt.canonical_bytes())
        digest.update(b64url_decode(self.player_sig.sig))
        digest.update(b64url_decode(self.venue_countersig.sig))
        return digest.hexdigest()


def sign_receipt(receipt: Receipt, player_key: KeyPair) -> Signature:
    """Produce the player_sig over the canonical receipt body."""
    sig = player_key.sign(receipt.canonical_bytes())
    return Signature(alg="ed25519", key_id=player_key.key_id, sig=b64url_encode(sig))


def countersign(receipt: Receipt, venue_key: KeyPair) -> Signature:
    """Produce the venue_countersig over the same canonical body bytes.

    The venue should perform out-of-band sanity checks (sequence is monotonic,
    screen belongs to this venue, etc.) before calling this — those checks
    are policy, not protocol.
    """
    sig = venue_key.sign(receipt.canonical_bytes())
    return Signature(alg="ed25519", key_id=venue_key.key_id, sig=b64url_encode(sig))


def build_unsigned_bundle(
    receipt: Receipt,
    player_sig: Signature,
    venue_countersig: Signature,
) -> SignedBundle:
    """Assemble a SignedBundle pre-anchoring (no etch_proof yet)."""
    return SignedBundle(
        receipt=receipt,
        player_sig=player_sig,
        venue_countersig=venue_countersig,
    )


def attach_proof(bundle: SignedBundle, proof: EtchProof) -> SignedBundle:
    """Return a new bundle with `etch_proof` populated."""
    return bundle.model_copy(update={"etch_proof": proof})


def bundle_to_dict(bundle: SignedBundle) -> Dict[str, Any]:
    """Serialize for on-disk persistence (uses model_dump)."""
    return bundle.model_dump(exclude_none=True)


def bundle_from_dict(data: Dict[str, Any]) -> SignedBundle:
    return SignedBundle.model_validate(data)
