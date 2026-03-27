"""
SQLAlchemy ORM models for Etch.

Tables:
  - etch_proofs: Legacy proof storage (v1/proof API)
  - etch_namespaces: Multi-tenant namespace isolation
  - etch_api_keys: API key authentication
  - etch_records: SoR record entries (v1/records API)
"""
from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Legacy (v1/proof) — kept for backward compatibility
# ---------------------------------------------------------------------------

class ProofRecord(Base):
    """Persistent storage for Etch content proofs (legacy)."""

    __tablename__ = "etch_proofs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    leaf_index = Column(Integer, nullable=False, unique=True)
    leaf_hash = Column(String(64), nullable=False)
    mmr_root = Column(String(64), nullable=False)
    leaf_count = Column(Integer, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    action_type = Column(String(50), nullable=False, default="content_proof")
    content_hash = Column(String(64), nullable=False, index=True)
    label = Column(String(200), nullable=True)
    owner = Column(String(200), nullable=True)
    proof_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at_exact = Column(Float, nullable=True)

    __table_args__ = (
        Index("idx_etch_leaf_index", "leaf_index", unique=True),
        Index("idx_etch_content_hash", "content_hash"),
        Index("idx_etch_owner", "owner"),
    )


# ---------------------------------------------------------------------------
# SoR primitives (v1/records)
# ---------------------------------------------------------------------------

class Namespace(Base):
    """Tenant namespace — each gets an isolated chain."""

    __tablename__ = "etch_namespaces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    namespace_id = Column(String(64), nullable=False, unique=True, index=True)  # ns_acme_corp
    name = Column(String(200), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ApiKey(Base):
    """API key scoped to a namespace."""

    __tablename__ = "etch_api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)  # SHA-256 of raw key
    key_prefix = Column(String(20), nullable=False)  # etch_live_sk_abc... (first 20 chars for display)
    namespace_id = Column(String(64), nullable=False, index=True)
    mode = Column(String(10), nullable=False, default="live")  # live | test
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    @staticmethod
    def generate(namespace_id: str, mode: str = "live") -> tuple[str, str]:
        """Generate a new API key. Returns (raw_key, key_hash)."""
        token = secrets.token_hex(24)  # 48 hex chars
        raw_key = f"etch_{mode}_sk_{token}"
        key_hash = _hash_key(raw_key)
        return raw_key, key_hash


class RecordEntry(Base):
    """SoR record entry — one per record commit."""

    __tablename__ = "etch_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(String(32), nullable=False, unique=True, index=True)  # rec_2xK9mQ4vB7nP
    namespace_id = Column(String(64), nullable=False, index=True)

    # Chain commitments
    leaf_index = Column(Integer, nullable=False)
    leaf_hash = Column(String(64), nullable=False)
    mmr_root = Column(String(64), nullable=False)
    chain_depth = Column(Integer, nullable=False)
    payload_hash = Column(String(64), nullable=False)

    # Record identity
    record_type = Column(String(200), nullable=True, index=True)  # salesforce.opportunity
    external_id = Column(String(200), nullable=True, index=True)  # 0065g00000XYZ
    record_hash = Column(String(64), nullable=False, index=True)  # SHA-256 of record.data

    # Metadata (stored, not hashed)
    metadata_json = Column(Text, nullable=True)  # JSON: actor, source, action, etc.

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at_exact = Column(Float, nullable=True)

    __table_args__ = (
        Index("idx_records_ns_type", "namespace_id", "record_type"),
        Index("idx_records_ns_ext", "namespace_id", "external_id"),
        Index("idx_records_ns_leaf", "namespace_id", "leaf_index"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_key(raw_key: str) -> str:
    import hashlib
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_record_id() -> str:
    """Generate a unique record ID like rec_2xK9mQ4vB7nP."""
    token = secrets.token_urlsafe(9)  # 12 chars base64
    return f"rec_{token}"
