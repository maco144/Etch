"""Unit + integration tests for etch.dooh — DOOH playback receipts.

Covers:
  - JCS canonicalization is deterministic.
  - ed25519 keypair save/load round-trips.
  - sign/countersign produce different sigs over the same canonical body.
  - bundle_hash is stable.
  - manifest resolve.
  - verifier happy path + every failure mode.
  - e2e: submit a bundle to a real Etch ASGI app and verify it.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from etch.dooh import (
    IdentityManifest,
    KeyPair,
    Receipt,
    SignedBundle,
    countersign,
    sign_receipt,
    verify_bundle,
)
from etch.dooh.canonical import canonicalize
from etch.dooh.keys import b64url_decode, b64url_encode, verify_signature
from etch.dooh.receipt import (
    EtchProof,
    Geo,
    Signature,
    attach_proof,
    build_unsigned_bundle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_receipt(sequence: int = 1, played_at: str | None = None) -> Receipt:
    return Receipt(
        advertiser_id="advertiser:acme",
        campaign_id="Q2-2026",
        creative_hash="sha256:" + "a" * 64,
        duration_ms=15000,
        played_at=played_at or "2026-04-27T18:42:13.000Z",
        screen_id="screen:nyc-01",
        sequence=sequence,
        venue_id="venue:outfront",
        geo=Geo(lat=40.758, lon=-73.985),
    )


def _make_etch_proof_for(bundle: SignedBundle, ts: float | None = None) -> EtchProof:
    """Build an internally-consistent EtchProof for a bundle (for offline-only tests).

    Mirrors etch.chain.AuditChain.append():
        leaf_hash = SHA256(prev_root : 'record_commit' : payload_hash : ts)
        mmr_root  = SHA256(prev_root : leaf_hash)
    """
    if ts is None:
        ts = time.time()
    bundle_hash = bundle.bundle_hash()
    # Use bundle_hash as the payload_hash for self-contained test proofs.
    # In production Etch wraps payload with namespace/registered_at, so production
    # payload_hash != bundle_hash. The verifier's step-3 check binds bundle to
    # record_hash; verify_inclusion_proof only checks proof internal consistency.
    payload_hash = bundle_hash
    prev_root = "0" * 64
    leaf = hashlib.sha256(f"{prev_root}:record_commit:{payload_hash}:{ts}".encode()).hexdigest()
    root = hashlib.sha256(f"{prev_root}:{leaf}".encode()).hexdigest()
    return EtchProof(
        namespace="ns_test",
        record_id="rec_test_0001",
        leaf_index=0,
        leaf_hash=leaf,
        mmr_root=root,
        prev_root=prev_root,
        payload_hash=payload_hash,
        timestamp=ts,
        record_hash=bundle_hash,
    )


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------

class TestCanonical:
    def test_deterministic_key_order(self):
        a = canonicalize({"b": 1, "a": 2})
        b = canonicalize({"a": 2, "b": 1})
        assert a == b == b'{"a":2,"b":1}'

    def test_unicode_passthrough(self):
        out = canonicalize({"name": "café"})
        assert "café".encode("utf-8") in out


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

class TestKeys:
    def test_generate_and_sign(self):
        k = KeyPair.generate("screen:test#k1")
        sig = k.sign(b"hello")
        assert verify_signature(k.public_b64, b"hello", sig)
        assert not verify_signature(k.public_b64, b"goodbye", sig)

    def test_save_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "k.key"
        k1 = KeyPair.generate("venue:test#k1")
        k1.save(path)
        # File should be 0600
        assert path.stat().st_mode & 0o777 == 0o600
        k2 = KeyPair.load(path)
        assert k2.key_id == k1.key_id
        assert k2.public_b64 == k1.public_b64
        # Both produce the same signature (deterministic ed25519)
        assert k1.sign(b"x") == k2.sign(b"x")

    def test_b64url_roundtrip(self):
        raw = b"\xff\xee\xdd\xcc\xbb\xaa"
        assert b64url_decode(b64url_encode(raw)) == raw

    def test_invalid_seed_length(self):
        with pytest.raises(ValueError):
            KeyPair.from_seed("k", b"too short")


# ---------------------------------------------------------------------------
# Receipt + bundle
# ---------------------------------------------------------------------------

class TestReceipt:
    def test_canonical_bytes_stable(self):
        r1 = _make_receipt()
        r2 = _make_receipt()
        assert r1.canonical_bytes() == r2.canonical_bytes()

    def test_geo_optional_omission(self):
        r = _make_receipt()
        r.geo = None
        body = r.canonical_bytes()
        assert b"geo" not in body

    def test_creative_hash_pattern_enforced(self):
        with pytest.raises(Exception):
            Receipt(
                advertiser_id="a",
                campaign_id="c",
                creative_hash="not-a-hash",
                duration_ms=1,
                played_at="2026-04-27T00:00:00.000Z",
                screen_id="s",
                sequence=0,
                venue_id="v",
            )

    def test_sign_and_countersign_over_same_bytes(self):
        r = _make_receipt()
        p = KeyPair.generate("screen:nyc-01#k1")
        v = KeyPair.generate("venue:outfront#k1")
        ps = sign_receipt(r, p)
        vs = countersign(r, v)
        assert ps.sig != vs.sig  # different keys
        body = r.canonical_bytes()
        assert verify_signature(p.public_b64, body, b64url_decode(ps.sig))
        assert verify_signature(v.public_b64, body, b64url_decode(vs.sig))

    def test_bundle_hash_deterministic(self):
        r = _make_receipt()
        p = KeyPair.generate("screen:nyc-01#k1")
        v = KeyPair.generate("venue:outfront#k1")
        ps = sign_receipt(r, p)
        vs = countersign(r, v)
        b1 = build_unsigned_bundle(r, ps, vs)
        b2 = build_unsigned_bundle(r, ps, vs)
        assert b1.bundle_hash() == b2.bundle_hash()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

class TestManifest:
    def test_resolve(self):
        m = IdentityManifest(advertiser_id="advertiser:acme", issued_at="2026-04-27T00:00:00Z")
        m.add_screen("screen:nyc-01#k1", "PUBKEYBASE64")
        m.add_venue("venue:outfront#k1", "OTHERPUB")
        assert m.resolve("screen:nyc-01#k1") == "PUBKEYBASE64"
        assert m.resolve("venue:outfront#k1") == "OTHERPUB"
        assert m.resolve("unknown") is None

    def test_save_load_roundtrip(self, tmp_path: Path):
        m = IdentityManifest(advertiser_id="advertiser:acme", issued_at="2026-04-27T00:00:00Z")
        m.add_screen("screen:nyc-01#k1", "AAA")
        path = tmp_path / "manifest.json"
        m.save(path)
        m2 = IdentityManifest.load(path)
        assert m2.resolve("screen:nyc-01#k1") == "AAA"
        assert m2.advertiser_id == "advertiser:acme"


# ---------------------------------------------------------------------------
# Verifier — happy path + failure modes
# ---------------------------------------------------------------------------

class TestVerifier:
    def _setup(self):
        p = KeyPair.generate("screen:nyc-01#k1")
        v = KeyPair.generate("venue:outfront#k1")
        m = IdentityManifest(advertiser_id="advertiser:acme", issued_at="2026-04-27T00:00:00Z")
        m.add_screen(p.key_id, p.public_b64)
        m.add_venue(v.key_id, v.public_b64)
        # Receipt's played_at is in 2026; for the verifier's step-6 check to
        # pass we need etch_proof.timestamp >= that, so use a fixed UTC ts.
        played = "2026-04-27T18:42:13.000Z"
        played_unix = datetime.fromisoformat(played.replace("Z", "+00:00")).timestamp()
        r = _make_receipt(played_at=played)
        ps = sign_receipt(r, p)
        vs = countersign(r, v)
        bundle = build_unsigned_bundle(r, ps, vs)
        proof = _make_etch_proof_for(bundle, ts=played_unix + 5)
        bundle = attach_proof(bundle, proof)
        return bundle, m

    def test_happy_path(self):
        bundle, m = self._setup()
        result = verify_bundle(bundle, m)
        assert result.ok, result.failed_steps
        # No trusted_root supplied, so step 5 yields a warning.
        assert any("trusted_root" in w for w in result.warnings)

    def test_trusted_root_match(self):
        bundle, m = self._setup()
        result = verify_bundle(bundle, m, trusted_root=bundle.etch_proof.mmr_root)
        assert result.ok
        assert not result.warnings

    def test_trusted_root_mismatch(self):
        bundle, m = self._setup()
        result = verify_bundle(bundle, m, trusted_root="0" * 64)
        assert not result.ok
        assert any("mmr_root" in s for s in result.failed_steps)

    def test_unknown_player_key(self):
        bundle, m = self._setup()
        m.screens.clear()
        result = verify_bundle(bundle, m)
        assert not result.ok
        assert any("player" in s for s in result.failed_steps)

    def test_unknown_venue_key(self):
        bundle, m = self._setup()
        m.venues.clear()
        result = verify_bundle(bundle, m)
        assert not result.ok
        assert any("venue" in s for s in result.failed_steps)

    def test_tampered_body_breaks_sigs(self):
        bundle, m = self._setup()
        # Mutate the receipt — sigs are over the original body
        bundle.receipt.duration_ms = 99999
        result = verify_bundle(bundle, m)
        assert not result.ok
        # The body change also changes bundle_hash, which trips step 3 first
        assert any("bundle_hash" in s or "sig" in s for s in result.failed_steps)

    def test_swapped_sig_fails(self):
        bundle, m = self._setup()
        # Replace player sig with garbage that decodes but doesn't verify
        bundle.player_sig = Signature(
            alg="ed25519",
            key_id="screen:nyc-01#k1",
            sig=b64url_encode(b"\x00" * 64),
        )
        result = verify_bundle(bundle, m)
        assert not result.ok

    def test_played_after_chain_anchor_fails(self):
        bundle, m = self._setup()
        # Force chain timestamp to be far before played_at
        old = bundle.etch_proof
        new_proof = old.model_copy(update={"timestamp": old.timestamp - 3600})
        bundle = bundle.model_copy(update={"etch_proof": new_proof})
        result = verify_bundle(bundle, m)
        assert not result.ok
        assert any("played_at" in s for s in result.failed_steps)

    def test_no_etch_proof(self):
        p = KeyPair.generate("screen:nyc-01#k1")
        v = KeyPair.generate("venue:outfront#k1")
        m = IdentityManifest(advertiser_id="advertiser:acme", issued_at="2026-04-27T00:00:00Z")
        m.add_screen(p.key_id, p.public_b64)
        m.add_venue(v.key_id, v.public_b64)
        r = _make_receipt()
        bundle = build_unsigned_bundle(r, sign_receipt(r, p), countersign(r, v))
        result = verify_bundle(bundle, m)
        assert not result.ok


# ---------------------------------------------------------------------------
# E2E — submit to a real Etch ASGI app
# ---------------------------------------------------------------------------

@pytest.fixture
async def etch_app_with_key():
    """Spin up a real Etch ASGI app with an in-memory DB and a bootstrapped namespace."""
    import etch.chain as chain_module
    import etch.chain_manager as cm_module
    import etch.db as db_module
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    orig = (db_module._session_maker, db_module._engine, chain_module._global_chain, cm_module._manager)
    db_module._session_maker = session_maker
    db_module._engine = engine
    chain_module._global_chain = None
    cm_module._manager = None

    from etch.models import Base
    from etch.server import app
    from etch.auth import bootstrap_namespace

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _, raw_key = await bootstrap_namespace("DOOH Test", namespace_id="ns_dooh_test")

    yield app, raw_key

    db_module._session_maker, db_module._engine, chain_module._global_chain, cm_module._manager = orig


@pytest.mark.asyncio
async def test_e2e_submit_and_verify(etch_app_with_key):
    """Submit a bundle to a real Etch namespace and verify it round-trip."""
    app, api_key = etch_app_with_key

    # Build keys, manifest, receipt, bundle.
    p = KeyPair.generate("screen:e2e#k1")
    v = KeyPair.generate("venue:e2e#k1")
    m = IdentityManifest(advertiser_id="advertiser:e2e", issued_at="2026-04-27T00:00:00Z")
    m.add_screen(p.key_id, p.public_b64)
    m.add_venue(v.key_id, v.public_b64)

    r = _make_receipt(played_at=datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
    bundle = build_unsigned_bundle(r, sign_receipt(r, p), countersign(r, v))

    # Submit via async httpx + ASGI transport, mimicking what EtchSubmitter does.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        bundle_hash = bundle.bundle_hash()
        create = await client.post(
            "/v1/records",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"record_hash": bundle_hash, "metadata": {"kind": "dooh-receipt"}},
        )
        assert create.status_code == 200, create.text
        rec = create.json()
        proof_resp = await client.get(
            f"/v1/records/{rec['id']}/proof",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert proof_resp.status_code == 200, proof_resp.text
        p_data = proof_resp.json()

    proof = EtchProof(
        namespace=rec["namespace"],
        record_id=rec["id"],
        leaf_index=p_data["leaf_index"],
        leaf_hash=p_data["leaf_hash"],
        mmr_root=p_data["mmr_root"],
        prev_root=p_data["prev_root"],
        payload_hash=p_data["payload_hash"],
        timestamp=p_data["timestamp"],
        record_hash=bundle_hash,
    )
    bundle = attach_proof(bundle, proof)

    # Verifier passes for sigs + step 3 (bundle_hash match) + step 6 (timing).
    # Step 4 (chain consistency) checks the proof's internal SHA hashes —
    # for production Etch payloads the wrapped payload_hash is NOT bundle_hash,
    # so we expect step 4 to fail here. That's a known v0 limitation noted in
    # docs/dooh-spec.md; the binding is closed by Etch's POST /v1/records/verify.
    result = verify_bundle(bundle, m)
    # Sigs and binding are correct:
    assert all("sig" not in s for s in result.failed_steps)
    assert all("bundle_hash" not in s for s in result.failed_steps)
    # Cross-check via Etch's verify endpoint (online, not offline):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        verify_resp = await client.post(
            "/v1/records/verify",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"record_id": rec["id"], "record_hash": bundle_hash},
        )
        assert verify_resp.status_code == 200
        body = verify_resp.json()
        assert body["verified"] is True
        assert body["content_match"] is True
        assert body["chain_integrity"] is True


# ---------------------------------------------------------------------------
# DOOH router endpoints
# ---------------------------------------------------------------------------

class TestDoohRouter:
    """Tests for /v1/dooh/* endpoints."""

    async def _build_bundle(self, played_at: str | None = None, screen: str = "screen:e2e",
                            campaign: str = "Q2-2026", sequence: int = 1) -> tuple[Receipt, "Signature", "Signature", IdentityManifest]:
        from etch.dooh.receipt import Geo
        p = KeyPair.generate(f"{screen}#k1")
        v = KeyPair.generate("venue:e2e#k1")
        m = IdentityManifest(advertiser_id="advertiser:e2e", issued_at="2026-04-27T00:00:00Z")
        m.add_screen(p.key_id, p.public_b64)
        m.add_venue(v.key_id, v.public_b64)
        r = Receipt(
            advertiser_id="advertiser:e2e",
            campaign_id=campaign,
            creative_hash="sha256:" + "a" * 64,
            duration_ms=15000,
            played_at=played_at or datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            screen_id=screen,
            sequence=sequence,
            venue_id="venue:e2e",
            geo=Geo(lat=40.758, lon=-73.985),
        )
        return r, sign_receipt(r, p), countersign(r, v), m

    async def test_post_receipts_anchors_and_returns_bundle_with_proof(self, etch_app_with_key):
        app, api_key = etch_app_with_key
        r, ps, vs, _m = await self._build_bundle()
        bundle = build_unsigned_bundle(r, ps, vs)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/v1/dooh/receipts",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"bundle": bundle.model_dump(exclude_none=True)},
            )
        assert resp.status_code == 200, resp.text
        returned = SignedBundle.model_validate(resp.json()["bundle"])
        assert returned.etch_proof is not None
        assert returned.etch_proof.record_hash == bundle.bundle_hash()
        assert returned.etch_proof.leaf_index == 0

    async def test_post_receipts_rejects_pre_anchored_bundle(self, etch_app_with_key):
        app, api_key = etch_app_with_key
        r, ps, vs, _m = await self._build_bundle()
        bundle = build_unsigned_bundle(r, ps, vs)
        # Force a fake etch_proof — should be rejected.
        bundle = attach_proof(bundle, EtchProof(
            namespace="ns_dooh_test", record_id="rec_x", leaf_index=0,
            leaf_hash="0" * 64, mmr_root="0" * 64, prev_root="0" * 64,
            payload_hash="0" * 64, timestamp=time.time(), record_hash=bundle.bundle_hash(),
        ))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/v1/dooh/receipts",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"bundle": bundle.model_dump(exclude_none=True)},
            )
        assert resp.status_code == 422

    async def test_get_receipts_filters_by_campaign_and_screen(self, etch_app_with_key):
        app, api_key = etch_app_with_key
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Submit 4 bundles: 2 campaigns × 2 screens
            for i, (campaign, screen) in enumerate([
                ("camp-A", "screen:01"), ("camp-A", "screen:02"),
                ("camp-B", "screen:01"), ("camp-A", "screen:01"),
            ]):
                r, ps, vs, _ = await self._build_bundle(screen=screen, campaign=campaign, sequence=i)
                bundle = build_unsigned_bundle(r, ps, vs)
                resp = await client.post(
                    "/v1/dooh/receipts",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"bundle": bundle.model_dump(exclude_none=True)},
                )
                assert resp.status_code == 200, resp.text

            # Filter: campaign=camp-A → 3 results
            r1 = await client.get(
                "/v1/dooh/receipts?campaign_id=camp-A",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert r1.status_code == 200
            assert len(r1.json()["data"]) == 3

            # Filter: campaign=camp-A AND screen=screen:01 → 2 results
            r2 = await client.get(
                "/v1/dooh/receipts?campaign_id=camp-A&screen_id=screen:01",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert r2.status_code == 200
            assert len(r2.json()["data"]) == 2

    async def test_get_receipts_filters_by_played_at_window(self, etch_app_with_key):
        app, api_key = etch_app_with_key
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Three bundles at different played_at times
            for ts in ["2026-04-27T10:00:00.000Z", "2026-04-27T12:00:00.000Z", "2026-04-27T14:00:00.000Z"]:
                r, ps, vs, _ = await self._build_bundle(played_at=ts)
                bundle = build_unsigned_bundle(r, ps, vs)
                resp = await client.post(
                    "/v1/dooh/receipts",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"bundle": bundle.model_dump(exclude_none=True)},
                )
                assert resp.status_code == 200, resp.text

            # Window 11:00 → 13:00 should match only the noon receipt
            resp = await client.get(
                "/v1/dooh/receipts?played_at_from=2026-04-27T11:00:00Z&played_at_to=2026-04-27T13:00:00Z",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert len(data) == 1
            assert data[0]["receipt"]["played_at"].startswith("2026-04-27T12:00:00")

    async def test_post_verify_runs_offline_verifier(self, etch_app_with_key):
        app, api_key = etch_app_with_key
        r, ps, vs, manifest = await self._build_bundle()
        bundle = build_unsigned_bundle(r, ps, vs)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            create = await client.post(
                "/v1/dooh/receipts",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"bundle": bundle.model_dump(exclude_none=True)},
            )
            anchored = SignedBundle.model_validate(create.json()["bundle"])

            verify = await client.post(
                "/v1/dooh/verify",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "bundle": anchored.model_dump(exclude_none=True),
                    "manifest": manifest.model_dump(),
                },
            )
        assert verify.status_code == 200
        body = verify.json()
        # Without trusted_root the verifier passes (sigs + binding + proof
        # internal consistency + timestamp ordering all hold), with a warning
        # that step 5 was skipped.
        assert body["ok"] is True, body["failed_steps"]
        assert any("trusted_root" in w for w in body["warnings"])

    async def test_post_verify_with_trusted_root(self, etch_app_with_key):
        app, api_key = etch_app_with_key
        r, ps, vs, manifest = await self._build_bundle()
        bundle = build_unsigned_bundle(r, ps, vs)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            create = await client.post(
                "/v1/dooh/receipts",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"bundle": bundle.model_dump(exclude_none=True)},
            )
            anchored = SignedBundle.model_validate(create.json()["bundle"])
            chain_root = await client.get(
                "/v1/chain/root",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            trusted_root = chain_root.json()["mmr_root"]

            verify = await client.post(
                "/v1/dooh/verify",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "bundle": anchored.model_dump(exclude_none=True),
                    "manifest": manifest.model_dump(),
                    "trusted_root": trusted_root,
                },
            )
        assert verify.status_code == 200
        body = verify.json()
        assert body["ok"] is True, body["failed_steps"]
        assert body["warnings"] == []
