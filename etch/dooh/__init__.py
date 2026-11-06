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
from .manifest import IdentityManifest
from .receipt import EtchProof, Receipt, SignedBundle, countersign, sign_receipt
from .submitter import EtchSubmitter
from .verifier import VerifyResult, verify_bundle

__all__ = [
    "EtchProof",
    "EtchSubmitter",
    "IdentityManifest",
    "KeyPair",
    "Receipt",
    "SignedBundle",
    "VerifyResult",
    "countersign",
    "sign_receipt",
    "verify_bundle",
]
