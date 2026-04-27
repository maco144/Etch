"""
JCS (RFC 8785) canonicalization for DOOH receipts.

The receipt body is canonicalized before signing so that both signers
produce signatures over identical bytes regardless of platform.
"""
from __future__ import annotations

from typing import Any, Mapping

import rfc8785


def canonicalize(obj: Mapping[str, Any]) -> bytes:
    """Return the RFC 8785 canonical JSON encoding of `obj`."""
    return rfc8785.dumps(obj)
