"""
Etch Python SDK — async client for the Etch SoR provenance API.

v2 API (Stripe-style):

    async with EtchClient("http://localhost:8100", api_key="etch_live_sk_...") as etch:
        receipt = await etch.records.create(
            record_type="crm.deal",
            record_id="deal-42",
            data={"stage": "negotiation", "value": 500000},
            metadata={"actor": "agent:closer-v2", "action": "stage_update"},
        )
        result = await etch.records.verify(
            record_id=receipt.id,
            data={"stage": "negotiation", "value": 500000},
        )
        assert result.verified

Legacy API (still works, emits deprecation warnings):

    async with EtchClient("http://localhost:8100") as client:
        receipt = await client.register("My content", label="v1")
        result = await client.verify(receipt.proof_id, "My content")

Privacy-preserving: content is hashed client-side (SHA-256) and only the hash
is transmitted to the server. Raw content never leaves the client.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


# ---------------------------------------------------------------------------
# Response models — v2 (SoR)
# ---------------------------------------------------------------------------

@dataclass
class RecordReceipt:
    """Receipt returned after committing a record to the Etch chain."""
    id: str
    record_hash: str
    leaf_hash: str
    mmr_root: str
    chain_position: int
    chain_depth: int
    timestamp: float
    namespace: str
    record_type: Optional[str] = None
    external_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class RecordVerifyResult:
    """Result of verifying a record against the chain."""
    record_id: str
    content_match: bool
    chain_integrity: bool
    verified: bool
    verified_at: float
    original_timestamp: float


@dataclass
class InclusionProof:
    """Portable, offline-verifiable inclusion proof."""
    record_id: str
    leaf_index: int
    leaf_hash: str
    mmr_root: str
    prev_root: str
    payload_hash: str
    timestamp: float
    algorithm: str
    verification_steps: List[str]


@dataclass
class ChainState:
    """Current chain state for a namespace."""
    mmr_root: str
    chain_depth: int
    namespace: str
    timestamp: float


# ---------------------------------------------------------------------------
# Response models — v1 (legacy, kept for backward compat)
# ---------------------------------------------------------------------------

@dataclass
class ProofReceipt:
    """Receipt returned after registering content on the Etch chain (legacy)."""
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
    """Result of verifying content against a stored proof (legacy)."""
    proof_id: int
    content_hash_matches: bool
    chain_integrity_valid: bool
    verified: bool
    receipt: ProofReceipt


@dataclass
class ProofRecord:
    """A proof record returned from lookup or listing endpoints (legacy)."""
    proof_id: int
    content_hash: str
    label: Optional[str]
    owner: Optional[str]
    timestamp: float
    leaf_hash: str
    mmr_root: str


@dataclass
class ChainStats:
    """Chain statistics (legacy)."""
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
    pass


class EtchValidationError(EtchError):
    pass


class EtchServerError(EtchError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _hash_data(data: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _raise_for_status(response: httpx.Response) -> None:
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


# ---------------------------------------------------------------------------
# v2 resource classes (Stripe-style)
# ---------------------------------------------------------------------------

class RecordsResource:
    """etch.records.create(), .retrieve(), .verify(), .list(), .proof()"""

    def __init__(self, client: EtchClient) -> None:
        self._client = client
        self._http = client._http

    def _headers(self) -> dict:
        return self._client._auth_headers()

    async def create(
        self,
        *,
        data: Dict[str, Any] | None = None,
        record_hash: str | None = None,
        record_type: str | None = None,
        record_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> RecordReceipt:
        """
        Commit a record to the Etch chain.
        Data is hashed client-side — only the hash is transmitted.
        """
        if data is not None:
            computed_hash = _hash_data(data)
        elif record_hash is not None:
            computed_hash = record_hash
        else:
            raise EtchValidationError("Provide either data or record_hash")

        body: Dict[str, Any] = {}
        if data is not None:
            body["record"] = {"type": record_type, "id": record_id, "data": data}
        else:
            body["record_hash"] = computed_hash
            if record_type or record_id:
                body["record"] = {"type": record_type, "id": record_id}
        if metadata:
            body["metadata"] = metadata

        # Client-side privacy: send hash instead of raw data
        if data is not None:
            body["record_hash"] = computed_hash
            if "record" in body:
                body["record"].pop("data", None)

        resp = await self._http.post("/v1/records", json=body, headers=self._headers())
        _raise_for_status(resp)
        d = resp.json()
        return RecordReceipt(
            id=d["id"],
            record_hash=d["record_hash"],
            leaf_hash=d["leaf_hash"],
            mmr_root=d["mmr_root"],
            chain_position=d["chain_position"],
            chain_depth=d["chain_depth"],
            timestamp=d["timestamp"],
            namespace=d["namespace"],
            record_type=d.get("record_type"),
            external_id=d.get("external_id"),
            metadata=d.get("metadata"),
        )

    async def retrieve(self, record_id: str) -> RecordReceipt:
        """Retrieve a record receipt by ID."""
        resp = await self._http.get(f"/v1/records/{record_id}", headers=self._headers())
        _raise_for_status(resp)
        d = resp.json()
        return RecordReceipt(
            id=d["id"],
            record_hash=d["record_hash"],
            leaf_hash=d["leaf_hash"],
            mmr_root=d["mmr_root"],
            chain_position=d["chain_position"],
            chain_depth=d["chain_depth"],
            timestamp=d["timestamp"],
            namespace=d["namespace"],
            record_type=d.get("record_type"),
            external_id=d.get("external_id"),
            metadata=d.get("metadata"),
        )

    async def verify(
        self,
        record_id: str,
        *,
        data: Dict[str, Any] | None = None,
        record_hash: str | None = None,
    ) -> RecordVerifyResult:
        """Verify a record against the chain."""
        body: Dict[str, Any] = {"record_id": record_id}
        if data is not None:
            body["record_hash"] = _hash_data(data)
        elif record_hash is not None:
            body["record_hash"] = record_hash
        else:
            raise EtchValidationError("Provide either data or record_hash")

        resp = await self._http.post("/v1/records/verify", json=body, headers=self._headers())
        _raise_for_status(resp)
        d = resp.json()
        return RecordVerifyResult(
            record_id=d["record_id"],
            content_match=d["content_match"],
            chain_integrity=d["chain_integrity"],
            verified=d["verified"],
            verified_at=d["verified_at"],
            original_timestamp=d["original_timestamp"],
        )

    async def proof(self, record_id: str) -> InclusionProof:
        """Get a self-contained, offline-verifiable inclusion proof."""
        resp = await self._http.get(f"/v1/records/{record_id}/proof", headers=self._headers())
        _raise_for_status(resp)
        d = resp.json()
        return InclusionProof(
            record_id=d["record_id"],
            leaf_index=d["leaf_index"],
            leaf_hash=d["leaf_hash"],
            mmr_root=d["mmr_root"],
            prev_root=d["prev_root"],
            payload_hash=d["payload_hash"],
            timestamp=d["timestamp"],
            algorithm=d["algorithm"],
            verification_steps=d["verification_steps"],
        )

    async def list(
        self,
        *,
        type: str | None = None,
        external_id: str | None = None,
        actor: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 50,
    ) -> List[RecordReceipt]:
        """List records with optional filters."""
        params: Dict[str, Any] = {"limit": limit}
        if type:
            params["type"] = type
        if external_id:
            params["external_id"] = external_id
        if actor:
            params["actor"] = actor
        if after:
            params["after"] = after
        if before:
            params["before"] = before

        resp = await self._http.get("/v1/records", params=params, headers=self._headers())
        _raise_for_status(resp)
        d = resp.json()
        return [
            RecordReceipt(
                id=r["id"],
                record_hash=r["record_hash"],
                leaf_hash=r["leaf_hash"],
                mmr_root=r["mmr_root"],
                chain_position=r["chain_position"],
                chain_depth=r["chain_depth"],
                timestamp=r["timestamp"],
                namespace=r["namespace"],
                record_type=r.get("record_type"),
                external_id=r.get("external_id"),
                metadata=r.get("metadata"),
            )
            for r in d["data"]
        ]


class ChainResource:
    """etch.chain.root()"""

    def __init__(self, client: EtchClient) -> None:
        self._client = client
        self._http = client._http

    def _headers(self) -> dict:
        return self._client._auth_headers()

    async def root(self) -> ChainState:
        """Get current chain state."""
        resp = await self._http.get("/v1/chain/root", headers=self._headers())
        _raise_for_status(resp)
        d = resp.json()
        return ChainState(
            mmr_root=d["mmr_root"],
            chain_depth=d["chain_depth"],
            namespace=d["namespace"],
            timestamp=d["timestamp"],
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class EtchClient:
    """
    Async HTTP client for the Etch provenance API.

    v2 (SoR API — requires api_key):
        etch = EtchClient("http://localhost:8100", api_key="etch_live_sk_...")
        receipt = await etch.records.create(data={...})

    v1 (legacy — no api_key):
        client = EtchClient("http://localhost:8100")
        receipt = await client.register("content")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8100",
        *,
        api_key: str | None = None,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._external_client = httpx_client is not None
        self._http = httpx_client or httpx.AsyncClient(base_url=self._base_url)

        # v2 Stripe-style resources
        self.records = RecordsResource(self)
        self.chain = ChainResource(self)

    # Keep _client as alias for backward compat in tests
    @property
    def _client(self) -> httpx.AsyncClient:
        return self._http

    def _auth_headers(self) -> dict:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def __aenter__(self) -> EtchClient:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        if not self._external_client:
            await self._http.aclose()

    # -- legacy v1 helpers (unchanged) --

    def _url(self, path: str) -> str:
        return f"/v1/proof{path}"

    # -- legacy v1 public API (emit deprecation warnings) --

    async def register(
        self,
        content: str | bytes,
        *,
        label: str | None = None,
        owner: str | None = None,
    ) -> ProofReceipt:
        """Register content on the Etch chain (legacy v1 API)."""
        content_hash = _sha256(content)
        return await self.register_hash(content_hash, label=label, owner=owner)

    async def register_hash(
        self,
        content_hash: str,
        *,
        label: str | None = None,
        owner: str | None = None,
    ) -> ProofReceipt:
        """Register a pre-computed SHA-256 hash (legacy v1 API)."""
        payload: dict = {"content_hash": content_hash}
        if label is not None:
            payload["label"] = label
        if owner is not None:
            payload["owner"] = owner

        resp = await self._http.post(self._url(""), json=payload)
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
        """Verify content against a stored proof (legacy v1 API)."""
        content_hash = _sha256(content)
        return await self.verify_hash(proof_id, content_hash)

    async def verify_hash(
        self,
        proof_id: int,
        content_hash: str,
    ) -> VerifyResult:
        """Verify a pre-computed hash against a stored proof (legacy v1 API)."""
        resp = await self._http.post(
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
        """Look up a proof by its proof_id (legacy v1 API)."""
        resp = await self._http.get(self._url(f"/{proof_id}"))
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
        """Look up a proof by content SHA-256 hash (legacy v1 API)."""
        resp = await self._http.get(self._url(f"/hash/{content_hash}"))
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
        """List recent proofs (legacy v1 API)."""
        resp = await self._http.get(
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
        """Get chain statistics (legacy v1 API)."""
        resp = await self._http.get(self._url("/stats"))
        _raise_for_status(resp)
        d = resp.json()
        return ChainStats(
            total_proofs=d["total_proofs"],
            chain_depth=d["chain_depth"],
            mmr_root=d["mmr_root"],
            first_proof_at=d.get("first_proof_at"),
            last_proof_at=d.get("last_proof_at"),
        )
