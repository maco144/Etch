"""Etch — Content provenance on a tamper-evident Merkle chain."""

from .chain import (
    AuditChain,
    ChainEntry,
    InclusionProof,
    verify_inclusion_proof,
)
from .sdk import EtchClient

__all__ = [
    "AuditChain",
    "ChainEntry",
    "EtchClient",
    "InclusionProof",
    "verify_inclusion_proof",
]

__version__ = "0.1.0"
