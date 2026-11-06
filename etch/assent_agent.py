"""
Assent for agents — ask a human to sign, from code that never shows Etch the document.

This is the sender half of the V2 send-to-sign flow (see ``assent-app/src/routes/Send.tsx``),
ported to Python so an agent can run it. It is wire-compatible with the browser on purpose:
the link it mints opens in the normal Assent Sign page, which decrypts, auto-detects fields,
signs, and PUTs the signed copy back. Nothing on the server knows an agent sent it.

    plaintext ── sha256 ──────────────────────────────► POST /v1/assent/stamp  (uploaded, index 0)
        │
        └─ AES-256-GCM, fresh key ──► [IV(12) || CT] ─► POST /v1/assent/document → doc_id, write_token
                                                          │
        sign_url = {base}/sign/{doc_id}#key={b64url(key)}&wt={write_token}

The key and the write token exist only here, in the returned ``sign_url``, and in the local
request file under ``ETCH_ASSENT_HOME`` (0600). Both ride in the URL fragment, which browsers
never transmit, so Etch holds ciphertext and hashes and nothing that opens them.

The request file is what later tools (status, fetch signed copy) read; it is the only copy of
the key, so losing it means the agent can no longer decrypt the signed return.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_BASE_URL = "https://assent.to"
IV_LENGTH = 12  # matches assent-app/src/lib/crypto.ts
MAX_PLAINTEXT_BYTES = 10 * 1024 * 1024  # matches Send.tsx; the server allows 15 MB of ciphertext


class AssentError(Exception):
    """A request the agent can act on: bad input, rate limit, server refusal."""


# ---------------------------------------------------------------------------
# Crypto — byte-for-byte the browser format
# ---------------------------------------------------------------------------

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """``[IV (12) || ciphertext+tag]``, the blob ``crypto.ts:decrypt`` expects."""
    iv = secrets.token_bytes(IV_LENGTH)
    return iv + AESGCM(key).encrypt(iv, plaintext, None)


def decrypt(blob: bytes, key: bytes) -> bytes:
    if len(blob) <= IV_LENGTH:
        raise AssentError("ciphertext too short to contain an IV")
    try:
        return AESGCM(key).decrypt(blob[:IV_LENGTH], blob[IV_LENGTH:], None)
    except InvalidTag:
        raise AssentError("ciphertext does not decrypt with this key (wrong key, or the blob was altered)") from None


def build_sign_link(base_url: str, document_id: str, key_b64: str, write_token: str) -> str:
    # Same shape as crypto.ts:buildSignLink. base64url and the server's token are both URL-safe.
    return f"{base_url.rstrip('/')}/sign/{document_id}#key={key_b64}&wt={write_token}"


# ---------------------------------------------------------------------------
# Text → PDF
#
# A dependency-free, single-font PDF writer: Letter pages, Helvetica, word-wrapped. It exists so
# an agent can ask for sign-off on plain text ("approve wiring $15K to Acme") without producing a
# PDF first. The signature block uses "Label: ____" lines because that is the exact pattern
# detectFields.ts turns into a signature field and text fields on the Sign page.
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = 612, 792
MARGIN = 72
BODY_SIZE = 11
TITLE_SIZE = 16
META_SIZE = 9
LEADING = 1.45

# Helvetica advance widths (1/1000 em) for printable ASCII, from the standard AFM.
_HELV_W = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,  # space - /
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,  # 0 - ?
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,  # @ - O
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,  # P - _
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,  # ` - o
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,  # p - ~
]


def _text_width(text: str, size: float, bold: bool = False) -> float:
    # Bold runs ~5% wider; close enough for wrapping, never used for placement.
    w = sum(_HELV_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text)
    return w * size / 1000 * (1.05 if bold else 1.0)


def _wrap(text: str, size: float, width: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        current = ""
        for word in para.split():
            candidate = f"{current} {word}" if current else word
            if _text_width(candidate, size, bold) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            # A single word wider than the line (URLs, hashes) is hard-broken.
            while _text_width(word, size, bold) > width:
                cut = max(1, int(len(word) * width / _text_width(word, size, bold)))
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def _pdf_str(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def text_to_pdf(
    title: str,
    body: str,
    *,
    requested_by: str | None = None,
    signer_name: str | None = None,
    created_at: datetime | None = None,
) -> bytes:
    """Render a sign-off document. Deterministic for a given input and ``created_at``."""
    created_at = created_at or datetime.now(UTC)
    usable = PAGE_W - 2 * MARGIN

    # (font, size, text, extra space before) — laid out top-down, paginated below.
    items: list[tuple[str, float, str, float]] = []
    for line in _wrap(title, TITLE_SIZE, usable, bold=True):
        items.append(("F2", TITLE_SIZE, line, 0))
    meta = f"Requested {created_at.strftime('%Y-%m-%d %H:%M UTC')}"
    if requested_by:
        meta += f" by {requested_by}"
    items.append(("F1", META_SIZE, meta, 6))
    first_body = True
    for line in _wrap(body.strip(), BODY_SIZE, usable):
        items.append(("F1", BODY_SIZE, line, 18 if first_body else 0))
        first_body = False

    # Signature block. Kept together on one page by the paginator.
    block = [
        ("F1", BODY_SIZE, "Signature: " + "_" * 40, 40),
        ("F1", BODY_SIZE, "Name: " + (signer_name or "_" * 40), 22),
        ("F1", BODY_SIZE, "Date: " + "_" * 24, 22),
    ]

    pages: list[list[tuple[str, float, str, float]]] = [[]]
    y = PAGE_H - MARGIN

    def place(item: tuple[str, float, str, float]) -> None:
        nonlocal y
        font, size, text, gap = item
        y -= gap + size * LEADING
        pages[-1].append((font, size, text, y))

    for item in items:
        if y - item[3] - item[1] * LEADING < MARGIN:
            pages.append([])
            y = PAGE_H - MARGIN
        place(item)
    block_h = sum(gap + size * LEADING for _, size, _, gap in block)
    if y - block_h < MARGIN:
        pages.append([])
        y = PAGE_H - MARGIN
    for item in block:
        place(item)

    # --- serialize -------------------------------------------------------
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    catalog = add(b"")  # filled once the page tree number is known
    pages_obj = add(b"")
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for page in pages:
        ops = []
        for font, size, text, ty in page:
            if text:
                ops.append(b"BT /%s %g Tf %d %.2f Td %s Tj ET" % (font.encode(), size, MARGIN, ty, _pdf_str(text)))
        stream = b"\n".join(ops)
        content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        kids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
            % (pages_obj, PAGE_W, PAGE_H, content, f1, f2)
        ))
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
    objects[pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    info = add(b"<< /Title %s /Producer (Etch Assent) /CreationDate (D:%s) >>" % (
        _pdf_str(title), created_at.strftime("%Y%m%d%H%M%SZ").encode()))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog, info, xref)
    return bytes(out)


# ---------------------------------------------------------------------------
# Local request store
# ---------------------------------------------------------------------------

def assent_home() -> Path:
    return Path(os.environ.get("ETCH_ASSENT_HOME", Path.home() / ".etch" / "assent"))


@dataclass
class SignatureRequest:
    document_id: str
    sign_url: str
    verify_url: str
    document_hash: str
    uploaded_record_id: str
    title: str
    signer: str | None
    created_at: str
    base_url: str
    # Secrets. Also inside sign_url; stored separately so later tools don't parse URLs.
    key: str
    write_token: str

    def public(self) -> dict[str, Any]:
        """What a tool returns. sign_url is included — the agent must deliver it — the raw key is not."""
        d = asdict(self)
        d.pop("key")
        d.pop("write_token")
        return d


def save_request(req: SignatureRequest) -> Path:
    directory = assent_home() / "requests"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{req.document_id}.json"
    # Create 0600 from the start so the key is never briefly world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(asdict(req), fh, indent=2)
    return path


def load_request(document_id: str) -> SignatureRequest:
    path = assent_home() / "requests" / f"{Path(document_id).name}.json"
    if not path.exists():
        raise AssentError(f"no local request for {document_id} under {path.parent}")
    return SignatureRequest(**json.loads(path.read_text()))


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

def _urls(base_url: str | None, api_url: str | None) -> tuple[str, str]:
    base = (base_url or os.environ.get("ETCH_ASSENT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    return base, (api_url or os.environ.get("ETCH_ASSENT_API_URL") or base).rstrip("/")


def _api_error(res: httpx.Response, what: str) -> AssentError:
    try:
        detail = res.json().get("detail") or res.text
    except ValueError:
        detail = res.text
    if res.status_code == 429:
        return AssentError(f"{what}: rate limited by Etch ({detail}). Anonymous Assent allows 10 uploads per IP "
                           "per hour; wait and retry.")
    return AssentError(f"{what} failed ({res.status_code}): {detail}")


async def request_signature(
    *,
    pdf: bytes | None = None,
    title: str | None = None,
    text: str | None = None,
    signer: str | None = None,
    requested_by: str | None = None,
    base_url: str | None = None,
    api_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> SignatureRequest:
    """Encrypt a document, park the ciphertext on Etch, bind its hash to the chain, mint a sign link.

    Give either ``pdf`` (bytes) or ``title`` + ``text`` (rendered to a one-page sign-off PDF).
    """
    base_url, api_url = _urls(base_url, api_url)
    created = datetime.now(UTC)

    if pdf is not None and text is not None:
        raise AssentError("give either a PDF or text, not both")
    if pdf is None:
        if not text or not text.strip():
            raise AssentError("nothing to sign: give a PDF, or a title and text")
        title = (title or "Approval request").strip()
        pdf = text_to_pdf(title, text, requested_by=requested_by, signer_name=signer, created_at=created)
    else:
        if not pdf.startswith(b"%PDF-"):
            raise AssentError("file is not a PDF (missing %PDF- header)")
        title = (title or "PDF signature request").strip()
    if len(pdf) > MAX_PLAINTEXT_BYTES:
        raise AssentError(f"PDF is {len(pdf) / 1024 / 1024:.1f} MB; the limit is 10 MB")

    document_hash = hashlib.sha256(pdf).hexdigest()
    key = AESGCM.generate_key(bit_length=256)
    ciphertext = encrypt(pdf, key)

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "etch-assent-agent"})
    try:
        res = await client.post(f"{api_url}/v1/assent/document", content=ciphertext,
                                headers={"Content-Type": "application/octet-stream"})
        if res.status_code != 200:
            raise _api_error(res, "upload")
        uploaded = res.json()
        document_id, write_token = uploaded["document_id"], uploaded["write_token"]

        # Event 0 binds the plaintext hash to the doc before the signer ever opens it — the Sign page
        # compares the decrypted bytes against this and warns on mismatch.
        res = await client.post(f"{api_url}/v1/assent/stamp", json={
            "kind": "assent.event",
            "schema_version": 1,
            "document_id": document_id,
            "event_type": "uploaded",
            "document_hash": document_hash,
            "parent_hash": None,
            "event_index": 0,
            "timestamp": created.isoformat().replace("+00:00", "Z"),
            "client_metadata": {"client": "etch-assent-agent", "source": "text" if text is not None else "pdf"},
        })
        if res.status_code != 200:
            raise _api_error(res, f"stamp (ciphertext for {document_id} is uploaded but unbound)")
        receipt = res.json()
    finally:
        if owns_client:
            await client.aclose()

    key_b64 = b64url(key)
    req = SignatureRequest(
        document_id=document_id,
        sign_url=build_sign_link(base_url, document_id, key_b64, write_token),
        # The Verify page resolves rec_… and doc_… ids only; server-issued document ids have no prefix, so
        # link the `uploaded` record, which resolves to the whole document chain.
        verify_url=f"{base_url}/verify/{receipt['id']}",
        document_hash=document_hash,
        uploaded_record_id=receipt["id"],
        title=title,
        signer=signer,
        created_at=created.isoformat(),
        base_url=base_url,
        key=key_b64,
        write_token=write_token,
    )
    save_request(req)
    return req


# ---------------------------------------------------------------------------
# Status — read the public chain for a document
# ---------------------------------------------------------------------------

# Progress the Sign page writes, in order: created on open, field_added/signed while signing,
# finalized once the flattened PDF exists (the signed copy is PUT back right after).
_STATES = ("sent", "opened", "signing", "finalized")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def _signer_summary(signer: dict[str, Any] | None) -> dict[str, Any] | None:
    if not signer:
        return None
    # The WebAuthn attestation blob is kilobytes of base64 and means nothing to an agent; the
    # credential id is enough to tell two passkeys apart. Name and email are typed by the signer.
    out = {k: signer[k] for k in ("method", "name", "email", "credential_id") if signer.get(k)}
    return out or None


def summarize_chain(chain: dict[str, Any], sent_hash: str | None) -> dict[str, Any]:
    events = chain["events"]
    types = {e["event_type"] for e in events}
    if "finalized" in types:
        state = "finalized"
    elif types & {"field_added", "signed", "countersigned"}:
        state = "signing"
    elif "created" in types:
        state = "opened"
    else:
        state = "sent"

    uploaded = next((e for e in events if e["event_type"] == "uploaded"), None)
    sent_hash = sent_hash or (uploaded or {}).get("document_hash")
    created = next((e for e in events if e["event_type"] == "created"), None)
    finalized = next((e for e in reversed(events) if e["event_type"] == "finalized"), None)
    signatures = [e for e in events if e["event_type"] in ("signed", "countersigned")]

    return {
        "document_id": chain["document_id"],
        "state": state,
        "chain_intact": chain["chain_intact"],
        # The Sign page hashes what it decrypted and stamps that as `created`. A mismatch means the
        # signer was shown different bytes from what was sent: the ciphertext was swapped.
        "opened_copy_matches_sent": None if created is None else created["document_hash"] == sent_hash,
        "sent_at": _iso(uploaded["server_timestamp"]) if uploaded else None,
        "opened_at": _iso(created["server_timestamp"]) if created else None,
        "signed_at": _iso(finalized["server_timestamp"]) if finalized else None,
        "signatures": [
            {"record_id": e["id"], "at": _iso(e["server_timestamp"]), "page": (e.get("location") or {}).get("page"),
             "signer": _signer_summary(e.get("signer"))}
            for e in signatures
        ],
        "finalized_record_id": finalized["id"] if finalized else None,
        "finalized_hash": finalized["document_hash"] if finalized else None,
        "events": [
            {"index": e["event_index"], "type": e["event_type"], "record_id": e["id"],
             "at": _iso(e["server_timestamp"])}
            for e in events
        ],
    }


async def _get_chain(client: httpx.AsyncClient, api_url: str, document_id: str) -> dict[str, Any]:
    res = await client.get(f"{api_url}/v1/assent/chain/{document_id}")
    if res.status_code == 404:
        raise AssentError(f"Etch has no events for {document_id}")
    if res.status_code != 200:
        raise _api_error(res, "chain lookup")
    return res.json()


def _client(client: httpx.AsyncClient | None) -> tuple[httpx.AsyncClient, bool]:
    if client is not None:
        return client, False
    return httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "etch-assent-agent"}), True


def _local_request(document_id: str) -> SignatureRequest | None:
    try:
        return load_request(document_id)
    except AssentError:
        return None


async def signature_status(
    document_id: str,
    *,
    api_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Where a document is in signing, from the public chain. Works for any document id; a local
    request (one this machine sent) adds the title and checks the opened copy against what was sent."""
    req = _local_request(document_id)
    base_url, api_url = _urls(req.base_url if req else None, api_url)
    client, owns = _client(client)
    try:
        chain = await _get_chain(client, api_url, document_id)
    finally:
        if owns:
            await client.aclose()
    status = summarize_chain(chain, req.document_hash if req else None)
    if req:
        status["title"] = req.title
        status["signer_expected"] = req.signer
    status["verify_url"] = f"{base_url}/verify/{status['finalized_record_id'] or chain['events'][0]['id']}"
    return status


# ---------------------------------------------------------------------------
# Fetch — download, decrypt, and check the signed copy against the chain
# ---------------------------------------------------------------------------

SIGNER_IDENTITY_NOTE = "Signer name and email are typed by the signer, not verified by Etch."


def check_proof(proof: dict[str, Any], expected_root: str, document_hash: str, document_id: str) -> dict[str, Any]:
    """Verify an Assent inclusion proof locally, trusting nothing Etch says.

    payload_hash = SHA256(payload_json) and payload_json names this document's hash and id, so the
    leaf is about this PDF; leaf_hash and mmr_root recompute from prev_root, so the leaf is in the chain.
    """
    payload_json = proof.get("payload_json")
    payload: dict[str, Any] = {}
    preimage = payload_json is not None and hashlib.sha256(payload_json.encode()).hexdigest() == proof["payload_hash"]
    if preimage:
        payload = json.loads(payload_json)
    leaf = hashlib.sha256(
        f"{proof['prev_root']}:record_commit:{proof['payload_hash']}:{proof['timestamp']}".encode()).hexdigest()
    root = hashlib.sha256(f"{proof['prev_root']}:{proof['leaf_hash']}".encode()).hexdigest()
    checks = {
        "payload_hash_recomputes": preimage,
        "payload_names_this_document": preimage and payload.get("record_hash") == document_hash
        and payload.get("external_id") == document_id,
        "leaf_hash_recomputes": leaf == proof["leaf_hash"],
        "mmr_root_recomputes": root == proof["mmr_root"],
        "root_matches_chain": proof["mmr_root"] == expected_root,
    }
    return {
        "record_id": proof["record_id"],
        "leaf_index": proof["leaf_index"],
        "mmr_root": proof["mmr_root"],
        "verified": all(checks.values()),
        **checks,
    }


async def fetch_signed(
    document_id: str,
    *,
    output_path: str | Path | None = None,
    api_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Download the signed copy, decrypt it with the locally stored key, and accept it only if its
    SHA-256 equals the `finalized` hash on the chain. Writes the PDF and returns where it went."""
    req = load_request(document_id)  # the key exists only here
    base_url, api_url = _urls(req.base_url, api_url)
    client, owns = _client(client)
    try:
        chain = await _get_chain(client, api_url, document_id)
        status = summarize_chain(chain, req.document_hash)
        if status["state"] != "finalized":
            raise AssentError(f"not signed yet (state: {status['state']}); check again with signature_status")

        res = await client.get(f"{api_url}/v1/assent/document/{document_id}")
        if res.status_code != 200:
            raise _api_error(res, "download")
        pdf = decrypt(res.content, b64url_decode(req.key))
        got = hashlib.sha256(pdf).hexdigest()
        if got == req.document_hash:
            raise AssentError(
                "the signer finalized, but Etch still holds the unsigned original: their browser's encrypted "
                "return failed. Ask them for the PDF their browser downloaded; its SHA-256 must equal "
                f"{status['finalized_hash']}.")
        if got != status["finalized_hash"]:
            raise AssentError(
                f"the stored copy (sha256 {got}) matches neither the document sent nor the finalized hash "
                f"{status['finalized_hash']} on the chain. Do not rely on it.")

        finalized = next(e for e in chain["events"] if e["id"] == status["finalized_record_id"])
        res = await client.get(f"{api_url}/v1/assent/records/{finalized['id']}/proof")
        if res.status_code != 200:
            raise _api_error(res, "proof lookup")
        proof = check_proof(res.json(), finalized["mmr_root"], got, document_id)
    finally:
        if owns:
            await client.aclose()

    path = Path(output_path).expanduser() if output_path else assent_home() / "signed" / f"{document_id}.pdf"
    if path.exists() and path.read_bytes() != pdf:
        raise AssentError(f"{path} already exists with different content; pass another output_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pdf)

    return {
        "document_id": document_id,
        "title": req.title,
        "path": str(path),
        "sha256": got,
        "matches_finalized_hash": True,
        "opened_copy_matches_sent": status["opened_copy_matches_sent"],
        "sent_at": status["sent_at"],
        "signed_at": status["signed_at"],
        "signatures": status["signatures"],
        "chain_intact": status["chain_intact"],
        "proof": proof,
        "signer_identity": SIGNER_IDENTITY_NOTE,
        "verify_url": f"{base_url}/verify/{finalized['id']}",
    }
