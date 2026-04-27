"""
etch.dooh — DOOH Playback Receipt SDK.

Implements docs/dooh-spec.md: bilaterally-signed playback receipts anchored
in an Etch namespace, offline-verifiable.

Public API:

    from etch.dooh import (
        KeyPair,
        Receipt,
        SignedBundle,
        IdentityManifest,
        EtchSubmitter,
        sign_receipt,
        countersign,
        verify_bundle,
        VerifyResult,
    )
"""
from .keys import KeyPair
from .receipt import Receipt, SignedBundle, EtchProof, sign_receipt, countersign
from .manifest import IdentityManifest
from .submitter import EtchSubmitter
from .verifier import verify_bundle, VerifyResult

__all__ = [
    "KeyPair",
    "Receipt",
    "SignedBundle",
    "EtchProof",
    "IdentityManifest",
    "EtchSubmitter",
    "sign_receipt",
    "countersign",
    "verify_bundle",
    "VerifyResult",
]
