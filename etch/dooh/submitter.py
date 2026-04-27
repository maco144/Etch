"""
Etch submitter — anchors a SignedBundle in an advertiser's namespace chain.

The submitter:
  1. Computes bundle_hash = SHA-256 over (canonical body || both sigs).
  2. POSTs `{record_hash, metadata}` to /v1/records.
  3. GETs /v1/records/{record_id}/proof for the inclusion proof.
  4. Returns an EtchProof populated for embedding into the bundle.

A single httpx.Client is reused so micro-batches share one HTTP keep-alive.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from .receipt import EtchProof, SignedBundle


@dataclass
class EtchSubmitter:
    """Sync HTTP client for anchoring signed bundles in Etch."""

    api_key: str
    base_url: str = "http://localhost:8100"
    timeout: float = 10.0
    _client: Optional[httpx.Client] = None

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "EtchSubmitter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def submit(self, bundle: SignedBundle) -> EtchProof:
        """Anchor `bundle` via the DOOH-specific endpoint.

        Stores the full bundle in Etch's namespace as record metadata so it can
        later be retrieved via `GET /v1/dooh/receipts`. For namespaces that
        prefer hash-only anchoring, use `submit_hash_only()` instead.
        """
        assert self._client is not None
        resp = self._client.post(
            "/v1/dooh/receipts",
            json={"bundle": bundle.model_dump(exclude_none=True)},
        )
        resp.raise_for_status()
        data = resp.json()
        proof_dict = data["bundle"]["etch_proof"]
        return EtchProof.model_validate(proof_dict)

    def submit_hash_only(self, bundle: SignedBundle) -> EtchProof:
        """Anchor only the bundle hash via /v1/records — bundle is NOT stored on Etch.

        Use this when the advertiser prefers to host bundles themselves.
        """
        assert self._client is not None
        bundle_hash = bundle.bundle_hash()

        create_resp = self._client.post(
            "/v1/records",
            json={
                "record_hash": bundle_hash,
                "metadata": {
                    "kind": "dooh-receipt",
                    "campaign_id": bundle.receipt.campaign_id,
                    "screen_id": bundle.receipt.screen_id,
                    "sequence": bundle.receipt.sequence,
                },
            },
        )
        create_resp.raise_for_status()
        created = create_resp.json()
        record_id = created["id"]
        namespace = created["namespace"]

        proof_resp = self._client.get(f"/v1/records/{record_id}/proof")
        proof_resp.raise_for_status()
        p = proof_resp.json()

        return EtchProof(
            namespace=namespace,
            record_id=record_id,
            leaf_index=p["leaf_index"],
            leaf_hash=p["leaf_hash"],
            mmr_root=p["mmr_root"],
            prev_root=p["prev_root"],
            payload_hash=p["payload_hash"],
            timestamp=p["timestamp"],
            record_hash=bundle_hash,
        )
