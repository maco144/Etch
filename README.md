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
# SQLite (zero config)
uvicorn etch.server:app --reload

# PostgreSQL
ETCH_DATABASE_URL=postgresql://user:pass@localhost/etch uvicorn etch.server:app
```

### Register content

```bash
curl -X POST http://localhost:8000/v1/proof \
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
curl -X POST http://localhost:8000/v1/proof/0/verify \
  -H "Content-Type: application/json" \
  -d '{"content": "My original article text"}'
```

### Privacy mode (hash only)

```bash
# Pre-hash locally — content never leaves your machine
HASH=$(echo -n "secret document" | sha256sum | cut -d' ' -f1)
curl -X POST http://localhost:8000/v1/proof \
  -H "Content-Type: application/json" \
  -d "{\"content_hash\": \"$HASH\"}"
```

---

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/proof` | Register content → proof receipt |
| `GET` | `/v1/proof/{proof_id}` | Retrieve receipt by ID |
| `GET` | `/v1/proof/hash/{content_hash}` | Look up by SHA-256 |
| `GET` | `/v1/proof/recent` | List recent proofs (paginated) |
| `GET` | `/v1/proof/stats` | Chain statistics |
| `POST` | `/v1/proof/{proof_id}/verify` | Verify content integrity |

---

## How it works

Etch uses a Merkle Mountain Range (MMR) hash chain. Each proof receipt commits to all previous entries:

```
leaf_hash = SHA256(prev_root : action_type : payload_hash : timestamp)
mmr_root  = SHA256(prev_root : leaf_hash)
```

If any historical entry is tampered with, all subsequent hashes become invalid. Proofs can be verified offline — no server trust required.

---

## As a library

```python
from etch import AuditChain, verify_inclusion_proof

chain = AuditChain()
entry = chain.append("content_proof", {"content_hash": "a3f1..."})

proof = chain.generate_proof(entry.leaf_index)
assert verify_inclusion_proof(proof)  # offline verification
```

---

## License

[FSL-1.1-Apache-2.0](https://fsl.software). Source-available for non-competing products. Converts to Apache 2.0 two years after each release.
