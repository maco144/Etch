"""Etch — Content provenance on a tamper-evident Merkle chain."""

from .chain import (
    AuditChain,
    ChainEntry,
    InclusionProof,
    verify_inclusion_proof,
)

__all__ = [
    "AuditChain",
    "ChainEntry",
    "InclusionProof",
    "verify_inclusion_proof",
]

__version__ = "0.1.0"
