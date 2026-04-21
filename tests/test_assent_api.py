"""Tests for the public, anonymous Etch Assent API (/v1/assent/*)."""
from __future__ import annotations

import hashlib

import httpx
import pytest


# ---------------------------------------------------------------------------
# Fixtures — mirror the pattern used in test_records_api.py
# ---------------------------------------------------------------------------

def _make_app():
    import etch.db as db_module
    import etch.chain as chain_module
    import etch.chain_manager as cm_module
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    orig_session_maker = db_module._session_maker
    orig_engine = db_module._engine
    db_module._session_maker = session_maker
    db_module._engine = engine

    orig_chain = chain_module._global_chain
    chain_module._global_chain = None

    orig_manager = cm_module._manager
    cm_module._manager = None

    from etch.server import app

    return app, engine, {
        "db_module": db_module,
        "chain_module": chain_module,
        "cm_module": cm_module,
        "orig_session_maker": orig_session_maker,
        "orig_engine": orig_engine,
        "orig_chain": orig_chain,
        "orig_manager": orig_manager,
    }


@pytest.fixture
async def http():
    app, engine, originals = _make_app()

    from etch.models import Base
    from etch.assent_api import ensure_assent_namespace, _limiter
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_assent_namespace()
    _limiter.reset()

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    yield client
    await client.aclose()

    originals["db_module"]._session_maker = originals["orig_session_maker"]
    originals["db_module"]._engine = originals["orig_engine"]
    originals["chain_module"]._global_chain = originals["orig_chain"]
    originals["cm_module"]._manager = originals["orig_manager"]


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _event(**overrides):
    base = {
        "kind": "assent.event",
        "schema_version": 1,
        "document_id": "doc_abc123",
        "event_type": "created",
        "document_hash": _sha("pdf-v0"),
        "parent_hash": None,
        "event_index": 0,
        "timestamp": "2026-04-21T00:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# POST /v1/assent/stamp
# ---------------------------------------------------------------------------

class TestStamp:
    async def test_accepts_anonymous_create(self, http: httpx.AsyncClient):
        res = await http.post("/v1/assent/stamp", json=_event())
        assert res.status_code == 200
        body = res.json()
        assert body["id"].startswith("rec_")
        assert body["event_index"] == 0
        assert body["namespace"] == "assent/public"
        assert body["parent_hash"] is None
        assert body["verification_url"].endswith(body["id"])

    async def test_parent_hash_required_for_later_events(self, http: httpx.AsyncClient):
        res = await http.post("/v1/assent/stamp", json=_event(event_index=1, parent_hash=None))
        assert res.status_code == 422
        assert "parent_hash" in res.text

    async def test_rejects_parent_on_first_event(self, http: httpx.AsyncClient):
        res = await http.post(
            "/v1/assent/stamp",
            json=_event(parent_hash=_sha("anything")),
        )
        assert res.status_code == 422

    async def test_rejects_bad_event_type(self, http: httpx.AsyncClient):
        res = await http.post("/v1/assent/stamp", json=_event(event_type="bogus"))
        assert res.status_code == 422

    async def test_rejects_bad_hash(self, http: httpx.AsyncClient):
        res = await http.post(
            "/v1/assent/stamp",
            json=_event(document_hash="not-a-hash"),
        )
        assert res.status_code == 422

    async def test_signer_and_location_roundtrip(self, http: httpx.AsyncClient):
        # Bare-create first event (first event cannot carry a signer/location).
        h0 = _sha("pdfv0")
        h1 = _sha("pdfv1")
        await http.post("/v1/assent/stamp", json=_event(document_hash=h0))

        signer = {"method": "webauthn", "credential_id": "abc", "email": "a@b.co"}
        loc = {"page": 1, "x": 12, "y": 34, "width": 200, "height": 60}
        res = await http.post(
            "/v1/assent/stamp",
            json=_event(
                event_type="signed",
                document_hash=h1,
                parent_hash=h0,
                event_index=1,
                signer=signer,
                location=loc,
            ),
        )
        assert res.status_code == 200
        body = res.json()
        assert body["signer"] == signer
        assert body["location"] == loc

        # Chain read-back also carries signer/location on the signed event.
        chain = (await http.get("/v1/assent/chain/doc_abc123")).json()
        signed = [e for e in chain["events"] if e["event_type"] == "signed"][0]
        assert signed["signer"]["credential_id"] == "abc"
        assert signed["location"]["page"] == 1

    async def test_chain_links_parent_to_previous(self, http: httpx.AsyncClient):
        h0 = _sha("pdf-v0")
        h1 = _sha("pdf-v1")
        r0 = await http.post("/v1/assent/stamp", json=_event(document_hash=h0))
        assert r0.status_code == 200
        r1 = await http.post(
            "/v1/assent/stamp",
            json=_event(
                event_type="signed",
                document_hash=h1,
                parent_hash=h0,
                event_index=1,
                signer={"method": "drawn", "name": "Alice"},
                location={"page": 1, "x": 10, "y": 20, "width": 200, "height": 60},
            ),
        )
        assert r1.status_code == 200

        chain = await http.get("/v1/assent/chain/doc_abc123")
        assert chain.status_code == 200
        body = chain.json()
        assert body["event_count"] == 2
        assert body["chain_intact"] is True
        assert body["events"][0]["event_type"] == "created"
        assert body["events"][1]["event_type"] == "signed"
        assert body["events"][1]["parent_hash"] == h0

    async def test_chain_intact_false_when_parent_mismatches(self, http: httpx.AsyncClient):
        # Seed a created event, then submit a signed event whose parent_hash
        # does NOT equal the previous event's document_hash. The stamp itself
        # is accepted (the public endpoint only enforces structural rules) but
        # the chain integrity view must flag the break.
        h0 = _sha("v0")
        wrong_parent = _sha("tampered")
        h1 = _sha("v1")
        await http.post("/v1/assent/stamp", json=_event(document_hash=h0))
        res = await http.post(
            "/v1/assent/stamp",
            json=_event(
                event_type="signed",
                document_hash=h1,
                parent_hash=wrong_parent,
                event_index=1,
            ),
        )
        assert res.status_code == 200
        chain = await http.get("/v1/assent/chain/doc_abc123")
        assert chain.json()["chain_intact"] is False

    async def test_rate_limit(self, http: httpx.AsyncClient):
        from etch.assent_api import RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS

        # Distinct documents so the per-document cap doesn't kick in first.
        for i in range(RATE_LIMIT_MAX_REQUESTS):
            res = await http.post(
                "/v1/assent/stamp",
                json=_event(
                    document_id=f"doc_rate_{i}",
                    document_hash=_sha(f"v{i}"),
                ),
            )
            assert res.status_code == 200

        res = await http.post(
            "/v1/assent/stamp",
            json=_event(document_id="doc_rate_over", document_hash=_sha("last")),
        )
        assert res.status_code == 429
        # Well-behaved clients read Retry-After to back off cleanly.
        assert res.headers.get("retry-after") == str(RATE_LIMIT_WINDOW_SECONDS)

    async def test_event_index_must_match_existing_count(self, http: httpx.AsyncClient):
        # count=0 → the only valid event_index is 0; skipping ahead is rejected.
        res = await http.post(
            "/v1/assent/stamp",
            json=_event(event_index=5, parent_hash=_sha("fake")),
        )
        # Parent-hash invariant fires first with 422 for non-first events that
        # still carry a parent_hash but have no predecessor... actually this one
        # DOES carry a parent_hash so the invariant accepts; monotonicity
        # catches it with 409.
        assert res.status_code == 409
        assert "event_index" in res.text

    async def test_rejects_document_id_squatting(self, http: httpx.AsyncClient):
        # First created event claims document_id → second attempt at event_index=0
        # for the same document_id must be rejected.
        await http.post("/v1/assent/stamp", json=_event())
        res = await http.post(
            "/v1/assent/stamp",
            json=_event(document_hash=_sha("squat")),
        )
        assert res.status_code == 409
        assert "event_index 0" in res.text or "expected 1" in res.text

    async def test_oversize_payload_rejected(self, http: httpx.AsyncClient):
        from etch.assent_api import MAX_PAYLOAD_BYTES

        big_blob = "x" * (MAX_PAYLOAD_BYTES + 1024)
        res = await http.post(
            "/v1/assent/stamp",
            json=_event(client_metadata={"junk": big_blob}),
        )
        assert res.status_code == 413


# ---------------------------------------------------------------------------
# GET /v1/assent/chain/{document_id}
# ---------------------------------------------------------------------------

class TestChainLookup:
    async def test_unknown_document_404(self, http: httpx.AsyncClient):
        res = await http.get("/v1/assent/chain/doc_does_not_exist")
        assert res.status_code == 404

    async def test_returns_ordered_events(self, http: httpx.AsyncClient):
        h0 = _sha("a")
        h1 = _sha("b")
        h2 = _sha("c")
        await http.post("/v1/assent/stamp", json=_event(document_hash=h0))
        await http.post(
            "/v1/assent/stamp",
            json=_event(
                event_type="field_added",
                document_hash=h0,
                parent_hash=h0,
                event_index=1,
                location={"page": 1, "x": 0, "y": 0, "width": 100, "height": 40},
            ),
        )
        await http.post(
            "/v1/assent/stamp",
            json=_event(
                event_type="signed",
                document_hash=h1,
                parent_hash=h0,
                event_index=2,
                signer={"method": "drawn"},
            ),
        )
        await http.post(
            "/v1/assent/stamp",
            json=_event(
                event_type="finalized",
                document_hash=h2,
                parent_hash=h1,
                event_index=3,
            ),
        )

        body = (await http.get("/v1/assent/chain/doc_abc123")).json()
        assert [e["event_index"] for e in body["events"]] == [0, 1, 2, 3]
        assert [e["event_type"] for e in body["events"]] == [
            "created",
            "field_added",
            "signed",
            "finalized",
        ]


class TestRecordLookup:
    async def test_fetch_single_record(self, http: httpx.AsyncClient):
        created = await http.post("/v1/assent/stamp", json=_event())
        rec_id = created.json()["id"]
        res = await http.get(f"/v1/assent/records/{rec_id}")
        assert res.status_code == 200
        assert res.json()["id"] == rec_id

    async def test_unknown_record_404(self, http: httpx.AsyncClient):
        res = await http.get("/v1/assent/records/rec_nope")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/assent/records/{record_id}/proof — public offline-verifiable proof
# ---------------------------------------------------------------------------

class TestPublicProof:
    async def test_proof_is_self_contained(self, http: httpx.AsyncClient):
        import hashlib as _h
        created = await http.post("/v1/assent/stamp", json=_event())
        rec_id = created.json()["id"]

        res = await http.get(f"/v1/assent/records/{rec_id}/proof")
        assert res.status_code == 200
        proof = res.json()

        # The proof should let us reconstruct leaf_hash and mmr_root using only
        # the formula published on the response — no server access needed.
        expected_leaf = _h.sha256(
            f"{proof['prev_root']}:record_commit:{proof['payload_hash']}:{proof['timestamp']}".encode()
        ).hexdigest()
        assert proof["leaf_hash"] == expected_leaf
        expected_root = _h.sha256(
            f"{proof['prev_root']}:{proof['leaf_hash']}".encode()
        ).hexdigest()
        assert proof["mmr_root"] == expected_root
        # First record: prev_root should be the chain genesis (64 zeros).
        assert proof["prev_root"] == "0" * 64
        assert proof["algorithm"] == "sha256"

    async def test_unknown_record_proof_404(self, http: httpx.AsyncClient):
        res = await http.get("/v1/assent/records/rec_nope/proof")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/assent/verify?hash= — recipient-side verify-by-hash
# ---------------------------------------------------------------------------

class TestVerifyByHash:
    async def test_hash_match_returns_events(self, http: httpx.AsyncClient):
        h0 = _sha("pdf-original")
        h1 = _sha("pdf-signed")
        # Stamp a created → signed chain.
        await http.post("/v1/assent/stamp", json=_event(document_hash=h0))
        await http.post(
            "/v1/assent/stamp",
            json=_event(
                event_type="signed",
                document_hash=h1,
                parent_hash=h0,
                event_index=1,
                signer={"method": "webauthn", "email": "alice@example.com"},
            ),
        )

        # Query by the finalized hash — should surface the signed event and
        # point the caller back at the document_id.
        res = await http.get("/v1/assent/verify", params={"hash": h1})
        assert res.status_code == 200
        body = res.json()
        assert body["match_count"] == 1
        assert body["document_ids"] == ["doc_abc123"]
        assert body["events"][0]["event_type"] == "signed"
        assert body["events"][0]["signer"]["email"] == "alice@example.com"

    async def test_unknown_hash_404(self, http: httpx.AsyncClient):
        unseen = _sha("never-signed")
        res = await http.get("/v1/assent/verify", params={"hash": unseen})
        assert res.status_code == 404
        assert "not found" in res.text.lower()

    async def test_rejects_bad_hash(self, http: httpx.AsyncClient):
        # Query param constraint kicks in: min_length=64, max_length=64.
        res = await http.get("/v1/assent/verify", params={"hash": "abc"})
        assert res.status_code == 422

    async def test_same_hash_across_documents(self, http: httpx.AsyncClient):
        # Two independent chains that happen to share a document_hash on the
        # created event (e.g. the same PDF uploaded twice). Verify should
        # surface both document_ids.
        shared = _sha("shared-pdf")
        await http.post(
            "/v1/assent/stamp",
            json=_event(document_id="doc_a", document_hash=shared),
        )
        await http.post(
            "/v1/assent/stamp",
            json=_event(document_id="doc_b", document_hash=shared),
        )
        res = await http.get("/v1/assent/verify", params={"hash": shared})
        assert res.status_code == 200
        body = res.json()
        assert set(body["document_ids"]) == {"doc_a", "doc_b"}
        assert body["match_count"] == 2
