"""
SQLAlchemy ORM models for Etch proof storage.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class ProofRecord(Base):
    """Persistent storage for Etch content proofs."""

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

    __table_args__ = (
        Index("idx_etch_leaf_index", "leaf_index", unique=True),
        Index("idx_etch_content_hash", "content_hash"),
        Index("idx_etch_owner", "owner"),
    )
