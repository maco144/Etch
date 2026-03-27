"""
Namespace-isolated chain manager for the Etch SoR API.

Each namespace gets its own AuditChain instance. Chains are created lazily
on first access and cached in memory. On restart, chains are rebuilt from
the database (the last known root and leaf count are loaded).
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

from sqlalchemy import select

from .chain import AuditChain
from .db import get_session
from .models import RecordEntry

logger = logging.getLogger(__name__)


class ChainManager:
    """
    Thread-safe registry of per-namespace AuditChain instances.

    Usage:
        manager = ChainManager()
        chain = await manager.get_chain("ns_acme_corp")
        entry = chain.append(...)
    """

    def __init__(self) -> None:
        self._chains: Dict[str, AuditChain] = {}
        self._lock = threading.Lock()

    async def get_chain(self, namespace_id: str) -> AuditChain:
        """
        Get or create the AuditChain for a namespace.
        On first access, loads the last known state from the database.
        """
        with self._lock:
            if namespace_id in self._chains:
                return self._chains[namespace_id]

        # Not cached — load from DB outside the lock
        chain = AuditChain()

        try:
            async with get_session() as session:
                # Get the last record for this namespace to restore chain state
                result = await session.execute(
                    select(RecordEntry)
                    .where(RecordEntry.namespace_id == namespace_id)
                    .order_by(RecordEntry.leaf_index.desc())
                    .limit(1)
                )
                last_record = result.scalar_one_or_none()

                if last_record is not None:
                    # Restore chain state from the last known record
                    chain._current_root = last_record.mmr_root
                    chain._leaf_count = last_record.leaf_index + 1
                    logger.info(
                        f"[Etch] Restored chain for {namespace_id}: "
                        f"depth={chain._leaf_count}, root={last_record.mmr_root[:12]}..."
                    )
        except Exception as exc:
            logger.warning(f"[Etch] Failed to restore chain for {namespace_id}: {exc}")

        with self._lock:
            # Double-check: another request may have created it while we were loading
            if namespace_id not in self._chains:
                self._chains[namespace_id] = chain
            return self._chains[namespace_id]

    def get_chain_sync(self, namespace_id: str) -> Optional[AuditChain]:
        """Get a cached chain without DB access. Returns None if not loaded yet."""
        with self._lock:
            return self._chains.get(namespace_id)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_manager: Optional[ChainManager] = None


def get_chain_manager() -> ChainManager:
    """Get or create the global ChainManager singleton."""
    global _manager
    if _manager is None:
        _manager = ChainManager()
    return _manager
