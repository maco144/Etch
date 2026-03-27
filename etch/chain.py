"""
Merkle Mountain Range (MMR) audit chain.

Provides tamper-evident sequential logging where each entry commits to
all previous entries via a hash chain. Any tampering with a historical
entry invalidates all subsequent entries.

Usage::

    chain = AuditChain()
    entry = chain.append(action_type="content_proof", payload={"content_hash": "a3f1..."})
    # entry.leaf_hash, entry.mmr_root, entry.leaf_index
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ChainEntry:
    leaf_index: int
    leaf_hash: str       # SHA-256 of (prev_root + action_type + payload_hash + timestamp)
    mmr_root: str        # SHA-256 of all leaf hashes up to this point (running root)
    payload_hash: str    # SHA-256 of the serialized payload
    action_type: str
    specialist: str
    agent_id: str
    created_at: float


@dataclass
class InclusionProof:
    """
    Compact proof that a ChainEntry was included at a specific position.

    The chain is a hash chain (not a tree), so inclusion is proven by the
    predecessor root: any verifier who independently computes
       leaf_hash = SHA256(prev_root : action : payload_hash : timestamp)
       mmr_root  = SHA256(prev_root : leaf_hash)
    and gets the same values can be confident the entry is authentic.
    """
    leaf_index: int
    leaf_hash: str
    mmr_root: str
    prev_root: str
    action_type: str
    payload_hash: str
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leaf_index": self.leaf_index,
            "leaf_hash": self.leaf_hash,
            "mmr_root": self.mmr_root,
            "prev_root": self.prev_root,
            "action_type": self.action_type,
            "payload_hash": self.payload_hash,
            "timestamp": self.timestamp,
        }


def verify_inclusion_proof(proof: InclusionProof) -> bool:
    """
    Verify an InclusionProof without trusting the server.

    Returns True if the proof is internally consistent (leaf_hash and
    mmr_root recompute correctly from prev_root + content + timestamp).
    The caller should additionally verify that proof.mmr_root matches a
    trusted chain root obtained out-of-band.
    """
    expected_leaf = hashlib.sha256(
        f"{proof.prev_root}:{proof.action_type}:{proof.payload_hash}:{proof.timestamp}".encode()
    ).hexdigest()
    if expected_leaf != proof.leaf_hash:
        return False
    expected_root = hashlib.sha256(f"{proof.prev_root}:{proof.leaf_hash}".encode()).hexdigest()
    return expected_root == proof.mmr_root


class AuditChain:
    """
    Thread-safe in-memory MMR audit chain with persistence hooks.

    The chain guarantees: if any historical entry is modified,
    all subsequent leaf_hash values will not match their expected values.

    For persistence, register a `persist_hook` that will be called
    synchronously after each append.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leaf_count: int = 0
        self._current_root: str = "0" * 64  # Genesis root (64 zero hex chars)
        self._persist_hook = None
        self._entries: list = []   # in-memory list for proof generation

    def set_persist_hook(self, hook) -> None:
        """Register a coroutine function hook(entry: ChainEntry) for persistence."""
        self._persist_hook = hook

    def append(
        self,
        action_type: str,
        payload: Dict[str, Any],
        specialist: str = "",
        agent_id: str = "",
    ) -> ChainEntry:
        """
        Append a new entry to the chain. Thread-safe.

        Returns the ChainEntry with all computed hashes.
        """
        with self._lock:
            now = time.time()
            payload_str = json.dumps(payload, sort_keys=True, default=str)
            payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()

            # leaf_hash commits to: previous root + action + payload + timestamp
            leaf_content = f"{self._current_root}:{action_type}:{payload_hash}:{now}"
            leaf_hash = hashlib.sha256(leaf_content.encode()).hexdigest()

            # New root commits to: previous root + new leaf
            new_root = hashlib.sha256(f"{self._current_root}:{leaf_hash}".encode()).hexdigest()

            entry = ChainEntry(
                leaf_index=self._leaf_count,
                leaf_hash=leaf_hash,
                mmr_root=new_root,
                payload_hash=payload_hash,
                action_type=action_type,
                specialist=specialist,
                agent_id=agent_id,
                created_at=now,
            )
            self._entries.append((entry, self._current_root))  # (entry, prev_root)

            self._leaf_count += 1
            self._current_root = new_root

            hook = self._persist_hook

        # Call the persist hook outside the lock to avoid deadlocks
        if hook is not None:
            try:
                hook(entry)
            except Exception:
                pass  # Persistence failures must never crash the chain

        return entry

    def current_root(self) -> str:
        """Return the current MMR root (read-only)."""
        with self._lock:
            return self._current_root

    def leaf_count(self) -> int:
        """Return the current number of entries."""
        with self._lock:
            return self._leaf_count

    def generate_proof(self, leaf_index: int) -> Optional[InclusionProof]:
        """
        Generate an InclusionProof for the entry at leaf_index.
        Returns None if the index is out of range.
        The proof can be verified offline with verify_inclusion_proof().
        """
        with self._lock:
            if leaf_index < 0 or leaf_index >= len(self._entries):
                return None
            entry, prev_root = self._entries[leaf_index]
        return InclusionProof(
            leaf_index=entry.leaf_index,
            leaf_hash=entry.leaf_hash,
            mmr_root=entry.mmr_root,
            prev_root=prev_root,
            action_type=entry.action_type,
            payload_hash=entry.payload_hash,
            timestamp=entry.created_at,
        )

    def verify_entry(self, entry: ChainEntry, prev_root: str) -> bool:
        """
        Verify that an entry is consistent with a known previous root.
        """
        expected_leaf_content = f"{prev_root}:{entry.action_type}:{entry.payload_hash}:{entry.created_at}"
        expected_leaf_hash = hashlib.sha256(expected_leaf_content.encode()).hexdigest()
        if entry.leaf_hash != expected_leaf_hash:
            return False
        expected_root = hashlib.sha256(f"{prev_root}:{entry.leaf_hash}".encode()).hexdigest()
        return entry.mmr_root == expected_root


# ---------------------------------------------------------------------------
# Global singleton chain
# ---------------------------------------------------------------------------

_global_chain: Optional[AuditChain] = None


def get_chain() -> AuditChain:
    """Get or create the global audit chain singleton."""
    global _global_chain
    if _global_chain is None:
        _global_chain = AuditChain()
    return _global_chain


def log_event(
    action_type: str,
    payload: Dict[str, Any],
    specialist: str = "",
    agent_id: str = "",
) -> ChainEntry:
    """
    Append an event to the global audit chain.

    This is the primary public API for audit logging.
    """
    return get_chain().append(
        action_type=action_type,
        payload=payload,
        specialist=specialist,
        agent_id=agent_id,
    )
