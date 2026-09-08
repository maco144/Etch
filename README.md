# Etch

**Content provenance on a tamper-evident Merkle chain. Register, verify, and prove content existed at a point in time.**

```bash
pip install etch
```

---

## Why Etch?

C2PA embeds provenance in media files. Blockchain attestation services cost gas and take minutes. Etch gives you cryptographic content provenance with a single HTTP call — sub-millisecond, privacy-preserving (content is never stored), and independently verifiable.

| Feature | Etch | C2PA | On-chain attestation |
|---------|:----:|:----:|:--------------------:|
| Sub-millisecond registration | Yes | N/A (embedded) | No (block time) |
| Content never stored | Yes | No (embedded in file) | Varies |
| Offline verification | Yes | Yes | Requires node |
| No gas fees | Yes | N/A | No |
| Tamper-evident chain | Yes | Yes | Yes |

---

## Quick start

### Start the server

```bash
# SQLite (zero config) — serves on :8100
uvicorn etch.server:app --reload --port 8100

# PostgreSQL
ETCH_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/etch uvicorn etch.server:app --port 8100
```

Interactive API docs: <http://localhost:8100/docs> (OpenAPI schema at `/openapi.json`).

### Register content

```bash
curl -X POST http://localhost:8100/v1/proof \
  -H "Content-Type: application/json" \
  -d '{"content": "My original article text", "label": "Blog: Launch Day"}'
```

Response:
```json
{
  "proof_id": 0,
  "content_hash": "a3f1...",
  "label": "Blog: Launch Day",
  "timestamp": 1710000000.0,
  "leaf_hash": "...",
  "mmr_root": "...",
  "chain_depth": 1
}
```

### Verify content

```bash
curl -X POST http://localhost:8100/v1/proof/0/verify \
  -H "Content-Type: application/json" \
  -d '{"content": "My original article text"}'
```

### Privacy mode (hash only)

```bash
# Pre-hash locally — content never leaves your machine
HASH=$(echo -n "secret document" | sha256sum | cut -d' ' -f1)
curl -X POST http://localhost:8100/v1/proof \
  -H "Content-Type: application/json" \
  -d "{\"content_hash\": \"$HASH\"}"
```

---

## The two chains

Etch exposes one primitive through two front doors:

| | `/v1/proof/*` | `/v1/records/*` |
|---|---|---|
| Chain | One global chain | One chain per namespace |
| Auth | None | API key (Bearer) |
| Intended for | Demos, public registration, single-tenant | System-of-record integrations, multi-tenant |
| Status | Legacy — kept stable | Current |

Everything else (`/v1/c2pa`, `/v1/dooh`, `/v1/assent`, `/g/`, watermark) is a domain-specific façade over one of those two.

---

## API

### `/v1/proof/*` — legacy registration (no auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/proof` | Register content → proof receipt |
| `POST` | `/v1/proof/batch` | Register up to 1000 items in one call |
| `GET` | `/v1/proof/{proof_id}` | Retrieve receipt by ID |
| `GET` | `/v1/proof/hash/{content_hash}` | Look up by SHA-256 |
| `GET` | `/v1/proof/recent` | List recent proofs (paginated) |
| `GET` | `/v1/proof/stats` | Chain statistics |
| `POST` | `/v1/proof/{proof_id}/verify` | Verify content integrity |

### `/v1/records/*` — System of Record API (API key, namespace-isolated)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/records` | Commit a record → receipt |
| `GET` | `/v1/records` | List/filter with cursor pagination |
| `GET` | `/v1/records/{record_id}` | Retrieve receipt |
| `GET` | `/v1/records/{record_id}/proof` | Self-contained inclusion proof |
| `POST` | `/v1/records/verify` | Verify a record against the chain |
| `GET` | `/v1/records/stats` | Append vs. dedup counters for this namespace |
| `GET` | `/v1/chain/root` | Current namespace chain state |

Keys look like `etch_{live|test}_sk_{token}` and are sent as `Authorization: Bearer …`. Only the SHA-256 of a key is stored. Provision the first namespace and key with:

```python
from etch.auth import bootstrap_namespace
namespace_id, api_key = await bootstrap_namespace("acme-crm")
```

```bash
curl -X POST http://localhost:8100/v1/records \
  -H "Authorization: Bearer etch_live_sk_…" \
  -H "Content-Type: application/json" \
  -d '{
        "record": {"type": "salesforce.opportunity", "id": "0065g00000XYZ", "data": {"stage": "Closed Won"}},
        "metadata": {"actor": "sync-job"},
        "if_changed": true
      }'
```

**`if_changed`** is opt-in de-duplication. With `record.id` set, Etch looks up the latest record for that `(namespace, external_id, record_type)`; if the `record_hash` is unchanged it returns the existing receipt with `"deduplicated": true` and appends nothing. It defaults to `false` on purpose — the chain is append-only and re-registering unchanged content is a legitimate timestamped re-attestation, so nothing is ever silently suppressed.

### `/v1/c2pa/*` — C2PA manifest bridge (no auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/c2pa/manifest` | Register a C2PA manifest on the chain |
| `GET` | `/v1/c2pa/manifest/{claim_id}` | Retrieve manifest + Etch proof |
| `POST` | `/v1/c2pa/verify` | Verify manifest against the chain |
| `POST` | `/v1/c2pa/bridge` | Bridge an existing proof into C2PA form |

### `/v1/dooh/*` — DOOH playback receipts (API key)

Bilaterally-signed proof that a creative played on a specific screen at a specific time.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/dooh/receipts` | Anchor a signed bundle |
| `GET` | `/v1/dooh/receipts` | Query by campaign, screen, time range |
| `POST` | `/v1/dooh/verify` | Server-side run of the offline verifier |

SDK and quickstart: [`etch/dooh/README.md`](etch/dooh/README.md). Protocol: [`docs/dooh-spec.md`](docs/dooh-spec.md) (CC-BY 4.0) — read the trust model first.

### `/v1/assent/*` — Etch Assent (anonymous, rate-limited)

Client-side PDF signing with an anonymous provenance chain. Plaintext PDFs are encrypted in the browser with AES-256-GCM; the key lives in the URL fragment, which browsers never transmit, so Etch cannot decrypt what it stores.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/assent/stamp` | Commit an assent event (20/hr per IP) |
| `GET` | `/v1/assent/chain/{document_id}` | Event chain + integrity check |
| `GET` | `/v1/assent/records/{record_id}` | Single receipt |
| `GET` | `/v1/assent/records/{record_id}/proof` | Public inclusion proof |
| `GET` | `/v1/assent/verify?hash={sha256}` | Find events by document hash |
| `POST`/`GET`/`PUT`/`HEAD` | `/v1/assent/document[/{doc_id}]` | Opaque ciphertext store (10 uploads/hr per IP, 15 MB) |

Product + protocol spec: [`docs/ETCH_ASSENT_SPEC.md`](docs/ETCH_ASSENT_SPEC.md). Frontend lives in [`assent-app/`](assent-app/) and builds to `site/assent/`.

### Glyph — shortcodes, sigils, and the public resolver

A shortcode is 8 Crockford-base32 characters encoding a 32-bit `proof_id` plus an 8-bit checksum over the content hash, so typos and fabrications are detectable before a DB lookup.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/proof/{proof_id}/glyph` | Render bar sigil + magic-link bundle |
| `GET` | `/g/{shortcode}` | Public resolver page (server-rendered HTML) |
| `GET` | `/g/{shortcode}.json` | Resolver result as JSON |
| `GET` | `/g/{shortcode}.png` | Bar sigil PNG |

### Watermark (`etchmark`) — inaudible audio pointer

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/proof/{proof_id}/embed-audio` | Multipart upload → watermarked 16-bit PCM WAV |
| `POST` | `/v1/proof/extract-audio` | Multipart upload → recovered shortcode + resolver URL |

Embed returns lossless WAV; the spread-spectrum layer survives the artist's own lossy re-encode. Extraction reads WAV/FLAC natively and MP3/AAC/Opus via `ffmpeg` if it's on `PATH`.

**The watermark is a pointer, not a proof.** It tells a verifier which Etch record claims a recording; the chain remains the only source of truth. The PRNG seed is public by design.

### Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | `{"status":"ok","service":"etch","version":"0.2.0"}` |

---

## How it works

Etch uses a Merkle Mountain Range (MMR) hash chain. Each proof receipt commits to all previous entries:

```
leaf_hash = SHA256(prev_root : action_type : payload_hash : timestamp)
mmr_root  = SHA256(prev_root : leaf_hash)
```

If any historical entry is tampered with, all subsequent hashes become invalid. Proofs can be verified offline — no server trust required. The chain is append-only: nothing is ever deleted or compacted.

---

## As a library

```python
from etch import AuditChain, verify_inclusion_proof

chain = AuditChain()
entry = chain.append("content_proof", {"content_hash": "a3f1..."})

proof = chain.generate_proof(entry.leaf_index)
assert verify_inclusion_proof(proof)  # offline verification
```

## Python SDK

```python
from etch import EtchClient

async with EtchClient(base_url="http://localhost:8100", api_key="etch_live_sk_…") as etch:
    receipt = await etch.records.create(
        record_type="invoice",
        record_id="INV-1001",
        data={"total": 4200},
        if_changed=True,
    )
    result = await etch.records.verify(receipt.id, data={"total": 4200})
```

`EtchClient.register()` / `.verify()` still target the legacy `/v1/proof` API and are deprecated in favour of `.records`.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ETCH_DATABASE_URL` / `DATABASE_URL` | `sqlite+aiosqlite:///./etch.db` | Database connection string |
| `ETCH_LOG_LEVEL` | `INFO` | Level for the `etch` loggers (dedup suppressions log here) |
| `ETCH_ASSENT_DOC_DIR` | `/var/etch/assent-documents` | Ciphertext blob storage for Assent send-to-sign |
| `ETCH_ASSENT_IP_SALT` | `etch-assent` | Salt for hashed client IPs in Assent rate limiting |

Optional extras: `pip install "etch[postgres]"` (asyncpg), `"etch[qr]"` (QR sigils), `"etch[dev]"`, `"etch[all]"`.

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                      # asyncio_mode = auto
ruff check etch/ tests/     # line-length 120, py311

uvicorn etch.server:app --reload --port 8100

cd assent-app && npm install && npm run dev   # Assent frontend
```

Release procedure: [`docs/RELEASING.md`](docs/RELEASING.md).

---

## License

[Rising Sun License v1.0](LICENSE.md). Free for personal use. Commercial deployments connect to the Nous network.

The DOOH SDK (`etch/dooh/`) is Apache-2.0; the DOOH spec is CC-BY 4.0.
