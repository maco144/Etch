"""
Etch Python SDK — async client for programmatic registration and verification.

Usage::

    async with EtchClient("http://localhost:8100") as client:
        receipt = await client.register("My original content", label="Article v1")
        result = await client.verify(receipt.proof_id, "My original content")
        assert result.verified

Privacy-preserving: content is hashed client-side (SHA-256) and only the hash
is transmitted to the server. Raw content never leaves the client.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

import httpx


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

@dataclass
class ProofReceipt:
    """Receipt returned after registering content on the Etch chain."""

    proof_id: int
    content_hash: str
    label: Optional[str]
    owner: Optional[str]
    timestamp: float
    leaf_hash: str
    mmr_root: str
    chain_depth: int


@dataclass
class VerifyResult:
    """Result of verifying content against a stored proof."""

    proof_id: int
    content_hash_matches: bool
    chain_integrity_valid: bool
    verified: bool
    receipt: ProofReceipt


@dataclass
class ProofRecord:
    """A proof record returned from lookup or listing endpoints."""

    proof_id: int
    content_hash: str
    label: Optional[str]
    owner: Optional[str]
    timestamp: float
    leaf_hash: str
    mmr_root: str


@dataclass
class ChainStats:
    """Chain statistics."""

    total_proofs: int
    chain_depth: int
    mmr_root: str
    first_proof_at: Optional[float]
    last_proof_at: Optional[float]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EtchError(Exception):
    """Base exception for all Etch SDK errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class EtchNotFoundError(EtchError):
    """Raised when a proof is not found (404)."""

    pass


class EtchValidationError(EtchError):
    """Raised when the server rejects input (422)."""

    pass


class EtchServerError(EtchError):
    """Raised when the server returns a 5xx error."""

    pass


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _sha256(data: str | bytes) -> str:
    """Compute SHA-256 hex digest of content."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _raise_for_status(response: httpx.Response) -> None:
    """Raise an appropriate EtchError for non-2xx responses."""
    if response.is_success:
        return

    detail = ""
    try:
        body = response.json()
        detail = body.get("detail", "")
    except Exception:
        detail = response.text

    status = response.status_code
    if status == 404:
        raise EtchNotFoundError(detail, status_code=status)
    elif status == 422:
        raise EtchValidationError(detail, status_code=status)
    elif status >= 500:
        raise EtchServerError(detail, status_code=status)
    else:
        raise EtchError(detail, status_code=status)


class EtchClient:
    """
    Async HTTP client for the Etch content provenance API.

    Args:
        base_url: Base URL of the Etch server (e.g. ``http://localhost:8100``).
        httpx_client: Optional pre-configured ``httpx.AsyncClient`` to use
            instead of creating one internally. Useful for testing with
            ``ASGITransport``.

    Example::

        async with EtchClient("http://localhost:8100") as client:
            receipt = await client.register("Hello, world!")
            print(receipt.proof_id, receipt.content_hash)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8100",
        *,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._external_client = httpx_client is not None
        self._client = httpx_client or httpx.AsyncClient(base_url=self._base_url)

    async def __aenter__(self) -> EtchClient:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client (only if we created it)."""
        if not self._external_client:
            await self._client.aclose()

    # -- helpers --

    def _url(self, path: str) -> str:
        return f"/v1/proof{path}"

    # -- public API --

    async def register(
        self,
        content: str | bytes,
        *,
        label: str | None = None,
        owner: str | None = None,
    ) -> ProofReceipt:
        """
        Register content on the Etch chain.

        Content is hashed client-side (SHA-256) -- only the hash is sent to
        the server, preserving privacy.
        """
        content_hash = _sha256(content)
        return await self.register_hash(content_hash, label=label, owner=owner)

    async def register_hash(
        self,
        content_hash: str,
        *,
        label: str | None = None,
        owner: str | None = None,
    ) -> ProofReceipt:
        """Register a pre-computed SHA-256 hash on the Etch chain."""
        payload: dict = {"content_hash": content_hash}
        if label is not None:
            payload["label"] = label
        if owner is not None:
            payload["owner"] = owner

        resp = await self._client.post(self._url(""), json=payload)
        _raise_for_status(resp)
        d = resp.json()
        return ProofReceipt(
            proof_id=d["proof_id"],
            content_hash=d["content_hash"],
            label=d.get("label"),
            owner=d.get("owner"),
            timestamp=d["timestamp"],
            leaf_hash=d["leaf_hash"],
            mmr_root=d["mmr_root"],
            chain_depth=d["chain_depth"],
        )

    async def verify(
        self,
        proof_id: int,
        content: str | bytes,
    ) -> VerifyResult:
        """
        Verify that content matches a stored proof.

        Content is hashed client-side -- only the hash is sent.
        """
        content_hash = _sha256(content)
        return await self.verify_hash(proof_id, content_hash)

    async def verify_hash(
        self,
        proof_id: int,
        content_hash: str,
    ) -> VerifyResult:
        """Verify a pre-computed hash against a stored proof."""
        resp = await self._client.post(
            self._url(f"/{proof_id}/verify"),
            json={"content_hash": content_hash},
        )
        _raise_for_status(resp)
        d = resp.json()
        receipt_d = d["receipt"]
        return VerifyResult(
            proof_id=d["proof_id"],
            content_hash_matches=d["content_hash_matches"],
            chain_integrity_valid=d["chain_integrity_valid"],
            verified=d["verified"],
            receipt=ProofReceipt(
                proof_id=receipt_d["proof_id"],
                content_hash=receipt_d["content_hash"],
                label=receipt_d.get("label"),
                owner=receipt_d.get("owner"),
                timestamp=receipt_d["timestamp"],
                leaf_hash=receipt_d["leaf_hash"],
                mmr_root=receipt_d["mmr_root"],
                chain_depth=receipt_d["chain_depth"],
            ),
        )

    async def lookup(self, proof_id: int) -> ProofRecord:
        """Look up a proof by its proof_id."""
        resp = await self._client.get(self._url(f"/{proof_id}"))
        _raise_for_status(resp)
        d = resp.json()
        return ProofRecord(
            proof_id=d["proof_id"],
            content_hash=d["content_hash"],
            label=d.get("label"),
            owner=d.get("owner"),
            timestamp=d["timestamp"],
            leaf_hash=d["leaf_hash"],
            mmr_root=d["mmr_root"],
        )

    async def lookup_hash(self, content_hash: str) -> ProofRecord:
        """Look up a proof by its content SHA-256 hash."""
        resp = await self._client.get(self._url(f"/hash/{content_hash}"))
        _raise_for_status(resp)
        d = resp.json()
        return ProofRecord(
            proof_id=d["proof_id"],
            content_hash=d["content_hash"],
            label=d.get("label"),
            owner=d.get("owner"),
            timestamp=d["timestamp"],
            leaf_hash=d["leaf_hash"],
            mmr_root=d["mmr_root"],
        )

    async def recent(self, limit: int = 20, offset: int = 0) -> list[ProofRecord]:
        """List recent proofs, newest first."""
        resp = await self._client.get(
            self._url("/recent"),
            params={"limit": limit, "offset": offset},
        )
        _raise_for_status(resp)
        d = resp.json()
        return [
            ProofRecord(
                proof_id=p["proof_id"],
                content_hash=p["content_hash"],
                label=p.get("label"),
                owner=p.get("owner"),
                timestamp=p["timestamp"],
                leaf_hash=p["leaf_hash"],
                mmr_root=p["mmr_root"],
            )
            for p in d["proofs"]
        ]

    async def stats(self) -> ChainStats:
        """Get chain statistics."""
        resp = await self._client.get(self._url("/stats"))
        _raise_for_status(resp)
        d = resp.json()
        return ChainStats(
            total_proofs=d["total_proofs"],
            chain_depth=d["chain_depth"],
            mmr_root=d["mmr_root"],
            first_proof_at=d.get("first_proof_at"),
            last_proof_at=d.get("last_proof_at"),
        )
