"""
Namespace-isolated chain manager for the Etch SoR API.

Each namespace gets its own AuditChain instance. Chains are created lazily
on first access and cached in memory. On restart, chains are rebuilt from
the database (the last known root and leaf count are loaded).

The legacy global chain (/v1/proof, /v1/c2pa) is restored the same way by
``restore_global_chain()``, which the server runs at startup.

A restore that can't reach the database raises instead of starting at genesis:
a chain that silently restarts at leaf 0 forks from everything already stored.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from .chain import AuditChain
from .chain import get_chain as get_global_chain
from .db import get_session
from .models import ProofRecord, RecordEntry

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
        self._chains: dict[str, AuditChain] = {}
        self._lock = threading.Lock()

    async def get_chain(self, namespace_id: str) -> AuditChain:
        """
        Get or create the AuditChain for a namespace.
        On first access, loads the last known state from the database.
        """
        with self._lock:
            if namespace_id in self._chains:
                return self._chains[namespace_id]

        # Not cached — load from DB outside the lock. A DB error propagates and nothing is cached,
        # so the request fails and the next one retries; it must never fall back to genesis.
        chain = AuditChain()
        async with get_session() as session:
            result = await session.execute(
                select(RecordEntry)
                .where(RecordEntry.namespace_id == namespace_id)
                .order_by(RecordEntry.leaf_index.desc())
                .limit(1)
            )
            last_record = result.scalar_one_or_none()
        if last_record is not None:
            chain.restore(last_record.leaf_index + 1, last_record.mmr_root)
            logger.info(
                f"[Etch] Restored chain for {namespace_id}: "
                f"depth={last_record.leaf_index + 1}, root={last_record.mmr_root[:12]}..."
            )

        with self._lock:
            # Double-check: another request may have created it while we were loading
            if namespace_id not in self._chains:
                self._chains[namespace_id] = chain
            return self._chains[namespace_id]

    def get_chain_sync(self, namespace_id: str) -> AuditChain | None:
        """Get a cached chain without DB access. Returns None if not loaded yet."""
        with self._lock:
            return self._chains.get(namespace_id)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_manager: ChainManager | None = None


def get_chain_manager() -> ChainManager:
    """Get or create the global ChainManager singleton."""
    global _manager
    if _manager is None:
        _manager = ChainManager()
    return _manager


async def restore_global_chain() -> AuditChain:
    """Load the legacy global chain's depth and root from the last ``etch_proofs`` row.

    Run once at startup, before any request can append. Raises if the database can't be read."""
    chain = get_global_chain()
    async with get_session() as session:
        last = (await session.execute(
            select(ProofRecord).order_by(ProofRecord.leaf_index.desc()).limit(1)
        )).scalar_one_or_none()
    if last is not None:
        chain.restore(last.leaf_index + 1, last.mmr_root)
        logger.info(f"[Etch] Restored global chain: depth={last.leaf_index + 1}, root={last.mmr_root[:12]}...")
    return chain
