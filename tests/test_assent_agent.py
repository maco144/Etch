"""Tests for the agent sender (etch.assent_agent) and its MCP wrapper (etch.assent_mcp).

The end-to-end test plays the browser recipient against the real Assent routers: it parses the
sign link the agent minted, fetches and decrypts the ciphertext exactly as Sign.tsx does, checks the
plaintext against the `uploaded` event, and PUTs a signed copy back with the fragment's write token.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from etch import assent_agent as agent


@pytest.fixture
async def http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ETCH_ASSENT_DOC_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("ETCH_ASSENT_HOME", str(tmp_path / "agent"))

    import etch.assent_docs_api as docs_mod
    importlib.reload(docs_mod)
    import etch.server as server_mod
    importlib.reload(server_mod)
    docs_mod._upload_limiter.reset()

    import etch.chain as chain_module
    import etch.chain_manager as cm_module
    import etch.db as db_module
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    orig = (db_module._session_maker, db_module._engine, chain_module._global_chain, cm_module._manager)
    db_module._session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_module._engine = engine
    chain_module._global_chain = None
    cm_module._manager = None

    from etch.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from etch.assent_api import _followon_limiter, _limiter, ensure_assent_namespace
    await ensure_assent_namespace()
    _limiter.reset()
    _followon_limiter.reset()

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server_mod.app), base_url="http://testserver")
    yield client, docs_mod
    await client.aclose()
    db_module._session_maker, db_module._engine, chain_module._global_chain, cm_module._manager = orig


async def _send(client, **kwargs):
    return await agent.request_signature(base_url="https://assent.to", api_url="http://testserver",
                                         client=client, **kwargs)


def _fragment(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).fragment).items()}


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class TestRequestSignature:
    async def test_recipient_can_open_verify_and_return(self, http):
        client, _ = http
        req = await _send(client, title="Approve vendor payment",
                          text="Wire $15,000 to Acme Corp for invoice 1042.", signer="Alex")

        # Link shape the Sign page parses: /sign/{doc_id}#key=…&wt=…
        parts = urlsplit(req.sign_url)
        assert parts.netloc == "assent.to"
        assert parts.path == f"/sign/{req.document_id}"
        frag = _fragment(req.sign_url)
        assert set(frag) == {"key", "wt"}

        # Recipient: download + decrypt with the fragment key, as crypto.ts does.
        res = await client.get(f"/v1/assent/document/{req.document_id}")
        assert res.status_code == 200
        plaintext = agent.decrypt(res.content, agent.b64url_decode(frag["key"]))
        assert plaintext.startswith(b"%PDF-1.4")
        assert hashlib.sha256(plaintext).hexdigest() == req.document_hash

        # Chain: event 0 is `uploaded`, carrying the plaintext hash the Sign page checks against.
        chain = (await client.get(f"/v1/assent/chain/{req.document_id}")).json()
        assert chain["event_count"] == 1 and chain["chain_intact"]
        ev = chain["events"][0]
        assert (ev["event_type"], ev["event_index"], ev["parent_hash"]) == ("uploaded", 0, None)
        assert ev["document_hash"] == req.document_hash
        assert ev["id"] == req.uploaded_record_id
        # verify_url must use an id the Verify page resolves (rec_… or doc_…).
        assert req.verify_url == f"https://assent.to/verify/{req.uploaded_record_id}"
        assert req.uploaded_record_id.startswith("rec_")

        # Recipient pushes a signed copy back with the fragment's write token.
        signed = agent.encrypt(plaintext + b"%signed", agent.b64url_decode(frag["key"]))
        res = await client.put(f"/v1/assent/document/{req.document_id}", content=signed,
                               headers={"Content-Type": "application/octet-stream", "X-Assent-Write-Token": frag["wt"]})
        assert res.status_code in (200, 204)

    async def test_existing_pdf_is_sent_unchanged(self, http):
        client, _ = http
        pdf = agent.text_to_pdf("Contract", "Terms.")
        req = await _send(client, pdf=pdf)
        res = await client.get(f"/v1/assent/document/{req.document_id}")
        key = agent.b64url_decode(_fragment(req.sign_url)["key"])
        assert agent.decrypt(res.content, key) == pdf
        assert req.document_hash == hashlib.sha256(pdf).hexdigest()

    async def test_request_file_is_private_and_reloadable(self, http, tmp_path):
        client, _ = http
        req = await _send(client, text="Deploy etch to rising.")
        path = tmp_path / "agent" / "requests" / f"{req.document_id}.json"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert agent.load_request(req.document_id) == req
        assert json.loads(path.read_text())["key"] == _fragment(req.sign_url)["key"]

    async def test_public_view_has_no_raw_secrets(self, http):
        client, _ = http
        req = await _send(client, text="ok")
        pub = req.public()
        assert "key" not in pub and "write_token" not in pub
        assert pub["sign_url"] == req.sign_url  # the agent still has to deliver it

    async def test_server_never_sees_plaintext(self, http, tmp_path):
        client, _ = http
        secret = "Patient MRN 448812 consents to procedure"
        await _send(client, text=secret)
        stored = b"".join(p.read_bytes() for p in (tmp_path / "docs").rglob("*") if p.is_file())
        assert secret.encode() not in stored and b"%PDF" not in stored

    async def test_rate_limit_is_reported_plainly(self, http):
        client, docs_mod = http
        for _ in range(docs_mod.UPLOAD_MAX_REQUESTS):
            await _send(client, text="x")
        with pytest.raises(agent.AssentError, match="rate limited"):
            await _send(client, text="x")


class TestInputValidation:
    @pytest.mark.parametrize("kwargs, msg", [
        ({}, "nothing to sign"),
        ({"text": "   "}, "nothing to sign"),
        ({"pdf": b"hello"}, "not a PDF"),
        ({"pdf": b"%PDF-1.4", "text": "x"}, "not both"),
        ({"pdf": b"%PDF-" + b"0" * agent.MAX_PLAINTEXT_BYTES}, "10 MB"),
    ])
    async def test_rejected_before_any_network_call(self, kwargs, msg):
        # No client, no server: a validation failure must not reach the network.
        with pytest.raises(agent.AssentError, match=msg):
            await agent.request_signature(api_url="http://127.0.0.1:9", **kwargs)


# ---------------------------------------------------------------------------
# Crypto + PDF
# ---------------------------------------------------------------------------

class TestCrypto:
    def test_blob_layout_matches_browser(self):
        key = os.urandom(32)
        blob = agent.encrypt(b"hello", key)
        assert len(blob) == 12 + 5 + 16  # IV || CT || GCM tag
        assert agent.decrypt(blob, key) == b"hello"

    def test_b64url_has_no_padding_and_roundtrips(self):
        key = os.urandom(32)
        enc = agent.b64url(key)
        assert "=" not in enc and "+" not in enc and "/" not in enc
        assert agent.b64url_decode(enc) == key


class TestTextToPdf:
    def test_signature_block_uses_detectable_labels(self):
        pdf = agent.text_to_pdf("T", "Body", signer_name="Alex")
        assert b"(Signature: ____" in pdf
        assert b"(Date: ____" in pdf
        assert b"(Name: Alex)" in pdf

    def test_long_text_paginates_and_xref_is_valid(self):
        pdf = agent.text_to_pdf("Long", "\n\n".join(["word " * 120] * 12))
        assert pdf.count(b"/Type /Page ") >= 2
        # Every xref offset must point at its "N 0 obj" header.
        start = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
        rows = pdf[start:].split(b"\n")[3:]
        for n, row in enumerate(rows, start=1):
            if not row.endswith(b" n "):
                break
            assert pdf[int(row[:10]):].startswith(b"%d 0 obj" % n)

    def test_escapes_and_non_latin(self):
        pdf = agent.text_to_pdf("a (b) \\c", "café — ☃")
        assert b"(a \\(b\\) \\\\c)" in pdf
        assert "café".encode("cp1252") in pdf

    def test_deterministic_for_fixed_time(self):
        from datetime import datetime, timezone
        t = datetime(2026, 11, 3, tzinfo=timezone.utc)
        assert agent.text_to_pdf("T", "B", created_at=t) == agent.text_to_pdf("T", "B", created_at=t)


# ---------------------------------------------------------------------------
# MCP surface
# ---------------------------------------------------------------------------

class TestMcp:
    async def test_tool_is_listed_with_schema(self):
        mcp_mod = pytest.importorskip("etch.assent_mcp")
        tools = {t.name: t for t in await mcp_mod.server.list_tools()}
        assert {"request_signature", "signature_status", "fetch_signed"} <= set(tools)
        assert set(tools["fetch_signed"].input_schema["properties"]) == {"document_id", "output_path"}
        props = tools["request_signature"].input_schema["properties"]
        assert {"text", "title", "pdf_path", "signer", "requested_by"} <= set(props)

    async def test_requires_exactly_one_source(self):
        mcp_mod = pytest.importorskip("etch.assent_mcp")
        from mcp.server.mcpserver.exceptions import ToolError
        with pytest.raises(ToolError, match="exactly one"):
            await mcp_mod.request_signature()
        with pytest.raises(ToolError, match="exactly one"):
            await mcp_mod.request_signature(text="x", pdf_path="/tmp/x.pdf")


# ---------------------------------------------------------------------------
# Status + fetch — the recipient side, replayed the way Sign.tsx writes it
# ---------------------------------------------------------------------------

async def _stamp(client, doc_id, index, event_type, doc_hash, parent, **extra):
    res = await client.post("/v1/assent/stamp", json={
        "kind": "assent.event", "schema_version": 1, "document_id": doc_id, "event_type": event_type,
        "document_hash": doc_hash, "parent_hash": parent, "event_index": index, **extra})
    assert res.status_code == 200, res.text


async def _recipient(client, req, *, stop_after: str = "finalized", put_back: bool = True,
                     opened_bytes: bytes | None = None, signed_bytes: bytes | None = None):
    """Open, sign, finalize, and return the copy, stopping after `stop_after`. Returns the signed bytes."""
    frag = _fragment(req.sign_url)
    key = agent.b64url_decode(frag["key"])
    plaintext = agent.decrypt((await client.get(f"/v1/assent/document/{req.document_id}")).content, key)
    opened = hashlib.sha256(opened_bytes or plaintext).hexdigest()
    doc = req.document_id
    await _stamp(client, doc, 1, "created", opened, req.document_hash)
    if stop_after == "created":
        return None
    loc = {"page": 1, "x": 72, "y": 600, "width": 245, "height": 36}
    await _stamp(client, doc, 2, "field_added", opened, opened, location=loc)
    await _stamp(client, doc, 3, "signed", opened, opened, location=loc,
                 signer={"method": "drawn", "name": "Alex", "email": "a@example.com"})
    if stop_after == "signed":
        return None
    signed = signed_bytes or plaintext + b"\n%signed by Alex\n"
    signed_hash = hashlib.sha256(signed).hexdigest()
    await _stamp(client, doc, 4, "finalized", signed_hash, opened)
    if put_back:
        res = await client.put(f"/v1/assent/document/{doc}", content=agent.encrypt(signed, key),
                               headers={"Content-Type": "application/octet-stream", "X-Assent-Write-Token": frag["wt"]})
        assert res.status_code in (200, 204)
    return signed


async def _status(client, doc_id):
    return await agent.signature_status(doc_id, api_url="http://testserver", client=client)


async def _fetch(client, doc_id, **kw):
    return await agent.fetch_signed(doc_id, api_url="http://testserver", client=client, **kw)


class TestSignatureStatus:
    @pytest.mark.parametrize("stop_after, state", [
        (None, "sent"), ("created", "opened"), ("signed", "signing"), ("finalized", "finalized")])
    async def test_states_follow_the_sign_page(self, http, stop_after, state):
        client, _ = http
        req = await _send(client, title="Approve deploy", text="Deploy HEAD.", signer="Alex")
        if stop_after:
            await _recipient(client, req, stop_after=stop_after)
        st = await _status(client, req.document_id)
        assert st["state"] == state
        assert st["chain_intact"]
        assert st["title"] == "Approve deploy" and st["signer_expected"] == "Alex"
        assert st["opened_copy_matches_sent"] is (None if state == "sent" else True)
        assert (st["signed_at"] is not None) is (state == "finalized")

    async def test_finalized_reports_signer_without_attestation_blob(self, http):
        client, _ = http
        req = await _send(client, text="x")
        await _recipient(client, req)
        st = await _status(client, req.document_id)
        assert st["signatures"][0]["signer"] == {"method": "drawn", "name": "Alex", "email": "a@example.com"}
        assert st["signatures"][0]["page"] == 1
        assert st["verify_url"] == f"https://assent.to/verify/{st['finalized_record_id']}"
        assert [e["type"] for e in st["events"]] == ["uploaded", "created", "field_added", "signed", "finalized"]

    async def test_swapped_ciphertext_is_flagged(self, http):
        client, _ = http
        req = await _send(client, text="Pay $100.")
        await _recipient(client, req, stop_after="created", opened_bytes=b"%PDF-1.4 Pay $100,000.")
        assert (await _status(client, req.document_id))["opened_copy_matches_sent"] is False

    async def test_works_without_local_request(self, http, tmp_path, monkeypatch):
        client, _ = http
        req = await _send(client, text="x")
        monkeypatch.setenv("ETCH_ASSENT_HOME", str(tmp_path / "elsewhere"))
        st = await _status(client, req.document_id)
        assert st["state"] == "sent" and "title" not in st

    async def test_unknown_document(self, http):
        client, _ = http
        with pytest.raises(agent.AssentError, match="no events"):
            await _status(client, "nope")


class TestFetchSigned:
    async def test_saves_signed_copy_that_matches_the_chain(self, http, tmp_path):
        client, _ = http
        req = await _send(client, title="Approve", text="Approve it.")
        signed = await _recipient(client, req)
        out = await _fetch(client, req.document_id)
        path = Path(out["path"])
        assert path == tmp_path / "agent" / "signed" / f"{req.document_id}.pdf"
        assert path.read_bytes() == signed
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert out["sha256"] == hashlib.sha256(signed).hexdigest()
        assert out["opened_copy_matches_sent"] is True
        assert out["proof"]["verified"]
        assert out["proof"]["payload_names_this_document"]
        assert "not verified" in out["signer_identity"]

    async def test_custom_path_and_idempotent(self, http, tmp_path):
        client, _ = http
        req = await _send(client, text="x")
        await _recipient(client, req)
        target = tmp_path / "out" / "signed.pdf"
        a = await _fetch(client, req.document_id, output_path=str(target))
        b = await _fetch(client, req.document_id, output_path=str(target))
        assert a["path"] == b["path"] == str(target)

    async def test_never_overwrites_a_different_file(self, http, tmp_path):
        client, _ = http
        req = await _send(client, text="x")
        await _recipient(client, req)
        target = tmp_path / "keep.pdf"
        target.write_bytes(b"mine")
        with pytest.raises(agent.AssentError, match="already exists"):
            await _fetch(client, req.document_id, output_path=str(target))
        assert target.read_bytes() == b"mine"

    @pytest.mark.parametrize("stop_after", ["created", "signed"])
    async def test_not_finalized_yet(self, http, stop_after):
        client, _ = http
        req = await _send(client, text="x")
        await _recipient(client, req, stop_after=stop_after)
        with pytest.raises(agent.AssentError, match="not signed yet"):
            await _fetch(client, req.document_id)

    async def test_finalized_but_return_upload_failed(self, http):
        client, _ = http
        req = await _send(client, text="x")
        await _recipient(client, req, put_back=False)
        with pytest.raises(agent.AssentError, match="still holds the unsigned original"):
            await _fetch(client, req.document_id)

    async def test_stored_copy_not_matching_chain_is_refused(self, http, tmp_path):
        client, _ = http
        req = await _send(client, text="x")
        signed = await _recipient(client, req, put_back=False)
        # Someone with the write token returns bytes other than the ones they finalized.
        frag = _fragment(req.sign_url)
        await client.put(f"/v1/assent/document/{req.document_id}",
                         content=agent.encrypt(signed + b"tampered", agent.b64url_decode(frag["key"])),
                         headers={"Content-Type": "application/octet-stream", "X-Assent-Write-Token": frag["wt"]})
        with pytest.raises(agent.AssentError, match="matches neither"):
            await _fetch(client, req.document_id)
        assert not (tmp_path / "agent" / "signed").exists()

    async def test_wrong_key_is_a_clear_error(self, http, tmp_path):
        client, _ = http
        req = await _send(client, text="x")
        await _recipient(client, req)
        path = tmp_path / "agent" / "requests" / f"{req.document_id}.json"
        data = json.loads(path.read_text())
        data["key"] = agent.b64url(os.urandom(32))
        path.write_text(json.dumps(data))
        with pytest.raises(agent.AssentError, match="does not decrypt"):
            await _fetch(client, req.document_id)

    async def test_requires_local_request(self, http, tmp_path, monkeypatch):
        client, _ = http
        req = await _send(client, text="x")
        monkeypatch.setenv("ETCH_ASSENT_HOME", str(tmp_path / "elsewhere"))
        with pytest.raises(agent.AssentError, match="no local request"):
            await _fetch(client, req.document_id)


class TestProofCheck:
    @staticmethod
    def _proof(doc_hash="ab" * 32, doc_id="doc_x"):
        prev, ts = "0" * 64, 1762207200.123456
        payload_json = json.dumps({"external_id": doc_id, "namespace": "assent_public", "record_hash": doc_hash,
                                   "record_type": "assent.event", "registered_at": ts - 1e-5}, sort_keys=True)
        payload = hashlib.sha256(payload_json.encode()).hexdigest()
        leaf = hashlib.sha256(f"{prev}:record_commit:{payload}:{ts}".encode()).hexdigest()
        root = hashlib.sha256(f"{prev}:{leaf}".encode()).hexdigest()
        return {"record_id": "rec_x", "leaf_index": 0, "prev_root": prev, "payload_hash": payload,
                "payload_json": payload_json, "timestamp": ts, "leaf_hash": leaf, "mmr_root": root}, root

    def test_valid_proof_verifies(self):
        proof, root = self._proof()
        assert agent.check_proof(proof, root, "ab" * 32, "doc_x")["verified"]

    @pytest.mark.parametrize("mutate, failing", [
        (lambda p: {**p, "payload_hash": "cd" * 32}, "payload_hash_recomputes"),
        (lambda p: {**p, "payload_json": p["payload_json"].replace("ab", "cd")}, "payload_hash_recomputes"),
        (lambda p: {**p, "payload_json": None}, "payload_names_this_document"),
        (lambda p: {**p, "timestamp": p["timestamp"] + 1}, "leaf_hash_recomputes"),
        (lambda p: {**p, "mmr_root": "ee" * 32}, "mmr_root_recomputes"),
    ])
    def test_tampering_fails_the_right_check(self, mutate, failing):
        proof, root = self._proof()
        result = agent.check_proof(mutate(proof), root, "ab" * 32, "doc_x")
        assert not result[failing] and not result["verified"]

    def test_proof_about_another_document_fails(self):
        proof, root = self._proof()
        assert not agent.check_proof(proof, root, "ff" * 32, "doc_x")["payload_names_this_document"]
        assert not agent.check_proof(proof, root, "ab" * 32, "doc_other")["payload_names_this_document"]

    def test_foreign_root_fails(self):
        proof, _ = self._proof()
        assert not agent.check_proof(proof, "ff" * 32, "ab" * 32, "doc_x")["root_matches_chain"]
