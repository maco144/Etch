"""
API key authentication for the Etch SoR API.

Keys are formatted as: etch_{mode}_sk_{token}
  - mode: "live" or "test"
  - token: 48 hex characters

Keys are stored as SHA-256 hashes. The raw key is never persisted.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException
from sqlalchemy import select

from .db import get_session
from .models import ApiKey, Namespace

logger = logging.getLogger(__name__)


@dataclass
class AuthContext:
    """Resolved authentication context for a request."""
    namespace_id: str
    namespace_name: str
    mode: str  # live | test


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def require_auth(authorization: str = Header(..., description="Bearer etch_live_sk_...")) -> AuthContext:
    """
    FastAPI dependency that validates an API key and returns the AuthContext.

    Usage in route:
        @router.post("/v1/records")
        async def create(auth: AuthContext = Depends(require_auth)):
            ...
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must be: Bearer etch_..._sk_...")

    raw_key = authorization[7:].strip()

    if not raw_key.startswith("etch_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_hash = _hash_key(raw_key)

    try:
        async with get_session() as session:
            result = await session.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash)
            )
            api_key = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[Etch] Auth DB lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Look up namespace
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Namespace).where(Namespace.namespace_id == api_key.namespace_id)
            )
            namespace = result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[Etch] Namespace lookup failed: {exc}")
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    if namespace is None:
        raise HTTPException(status_code=401, detail="Namespace not found for this API key")

    return AuthContext(
        namespace_id=api_key.namespace_id,
        namespace_name=namespace.name,
        mode=api_key.mode,
    )


# ---------------------------------------------------------------------------
# Bootstrap helpers (for dev / first-run)
# ---------------------------------------------------------------------------

async def bootstrap_namespace(name: str, namespace_id: str | None = None) -> tuple[str, str]:
    """
    Create a namespace and its first API key. Returns (namespace_id, raw_api_key).
    Used for dev setup and first-run provisioning.
    """
    import secrets

    ns_id = namespace_id or f"ns_{secrets.token_hex(8)}"
    raw_key, key_hash = ApiKey.generate(ns_id, mode="live")

    async with get_session() as session:
        session.add(Namespace(namespace_id=ns_id, name=name))
        session.add(ApiKey(
            key_hash=key_hash,
            key_prefix=raw_key[:20],
            namespace_id=ns_id,
            mode="live",
        ))

    return ns_id, raw_key
