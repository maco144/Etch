# etch.dooh — DOOH Playback Receipt SDK

Python SDK for cryptographically verifiable digital out-of-home (DOOH) ad
playback receipts. Drops into existing signage stacks; anchors bilaterally-
signed receipts in an Etch namespace; produces offline-verifiable proof
bundles for advertisers and auditors.

**Spec:** [`docs/dooh-spec.md`](../../docs/dooh-spec.md) — read this first.
The trust model section is non-negotiable context.

## What this is for

Today, DOOH ad delivery is *measured* — audience modeling, vehicle counts,
mobile-device proxies. There is no cryptographic proof that a creative ran on
a specific screen at a specific time. This SDK closes that gap with:

- **Bilateral signing** — every play gets a receipt signed by both the player
  (screen) and the venue (media owner). Neither side can unilaterally forge.
- **Etch anchoring** — every signed bundle's hash is committed to an MMR
  chain in an advertiser-owned namespace. Tamper-evident, append-only.
- **Offline verifier** — anyone with the bundle, the advertiser's identity
  manifest, and the namespace's published chain root can verify the receipt.
  No platform trust required.

## Quickstart

### 1. Sign + anchor a single play

```python
from etch.dooh import (
    EtchSubmitter, KeyPair, Receipt,
    sign_receipt, countersign,
)
from etch.dooh.receipt import Geo, build_unsigned_bundle

player = KeyPair.generate("screen:nyc-01#k1")
venue  = KeyPair.generate("venue:outfront#k1")

receipt = Receipt(
    advertiser_id="advertiser:acme",
    campaign_id="Q2-2026",
    creative_hash="sha256:" + "a" * 64,
    duration_ms=15_000,
    played_at="2026-04-27T18:42:13.000Z",
    screen_id="screen:nyc-01",
    sequence=1,
    venue_id="venue:outfront",
    geo=Geo(lat=40.758, lon=-73.985),
)

bundle = build_unsigned_bundle(
    receipt,
    sign_receipt(receipt, player),
    countersign(receipt, venue),
)

with EtchSubmitter(api_key="etch_live_sk_…", base_url="http://localhost:8100") as s:
    proof = s.submit(bundle)   # POST /v1/dooh/receipts; bundle is stored too

print(proof.record_id, proof.mmr_root[:16])
```

### 2. Verify a bundle offline

```python
from etch.dooh import IdentityManifest, SignedBundle, verify_bundle

bundle = SignedBundle.model_validate_json(open("play.bundle.json").read())
manifest = IdentityManifest.load("advertiser-manifest.json")
trusted_root = "..."  # fetched out-of-band from the advertiser's published feed

result = verify_bundle(bundle, manifest, trusted_root=trusted_root)
print(result.ok, result.failed_steps)
```

### 3. End-to-end demo (no separate server required)

```bash
python examples/dooh_demo.py --plays 100
# Anchors 100 receipts, queries them back, verifies all of them.
```

The reference player (`examples/dooh_reference_player.py`) drives a real Etch
endpoint:

```bash
python examples/dooh_reference_player.py \
  --etch-url http://localhost:8100 \
  --etch-key etch_live_sk_… \
  --campaign Q2-2026-launch \
  --plays 25 \
  --out ./bundles/
```

## Architecture

| Module | Role |
|---|---|
| `canonical.py` | RFC 8785 (JCS) wrapper — deterministic body bytes for signing |
| `keys.py` | `KeyPair` — ed25519 generate/save/load/sign + `verify_signature` |
| `receipt.py` | `Receipt`, `SignedBundle`, `EtchProof`, sign/countersign helpers |
| `manifest.py` | `IdentityManifest` — advertiser-published `key_id → public key` map |
| `submitter.py` | `EtchSubmitter` — sync httpx client; `submit()` (DOOH endpoint, stores bundle) and `submit_hash_only()` (privacy-preserving) |
| `verifier.py` | `verify_bundle()` — six-step offline verification |

## HTTP endpoints

The Etch server exposes three DOOH-specific endpoints (in addition to the
generic `/v1/records/*`):

| Endpoint | Purpose |
|---|---|
| `POST /v1/dooh/receipts` | Anchor a SignedBundle, store it, return bundle + proof |
| `GET  /v1/dooh/receipts` | Query bundles by `campaign_id`, `screen_id`, `played_at_from`, `played_at_to` |
| `POST /v1/dooh/verify`   | Server-side run of the offline verifier (for callers who don't want the SDK) |

## Two anchoring modes

| Mode | Submitter call | Etch stores | When to use |
|---|---|---|---|
| **Stored** (default) | `submitter.submit(bundle)` | Hash + full bundle JSON | Managed verification — query bundles back via `GET /v1/dooh/receipts` |
| **Hash-only** | `submitter.submit_hash_only(bundle)` | Hash only | Privacy-sensitive — advertiser hosts bundles themselves |

Both modes produce the same bundle hash anchored on the same chain. Choice
is purely about where the bundle JSON lives.

## Key design decisions

- **Drop Concord for v0.** The bilateral-receipt primitive needed here
  (two-party signature over a payload) is simpler than Concord's accord
  (two parties at the same `worldRoot`) and doesn't need a Concord runtime.
  See the spec's *Trust model* section.
- **Advertiser is the trust anchor.** Each advertiser publishes its own
  identity manifest mapping screen/venue `key_id`s to ed25519 public keys.
  No global PKI, no mandatory registry.
- **One namespace per advertiser.** Etch chains are namespace-isolated;
  one advertiser's chain cannot affect another's.
- **Receipt-per-play, not Merkle-of-Merkles batched leaves.** Micro-batching
  is an SDK transport detail; each play still gets its own MMR leaf.
- **Cooperative fraud is a documented limit, not a hidden one.** The spec's
  *What this protocol does NOT prove* section names it explicitly. Mitigation
  path: three-party receipts → sensors → hardware attestation.

## Testing

```bash
pytest tests/test_dooh.py     # 29 tests: unit + integration + e2e
ruff check etch/dooh/
```

## Roadmap (post-MVP)

- Three-party receipts (SSP/auditor co-signer)
- Hardware attestation (TPM-rooted player keys)
- Sensor-backed proof-of-display (camera/audio fingerprint)
- Etch-anchored manifest signing for historical key resolution
- Optional Concord-backed identity layer for venues already on Concord
- C2PA bridge for AI-generated creatives (Etch already has the primitive)

## License

- **SDK code** — Apache-2.0
- **Spec** (`docs/dooh-spec.md`) — CC-BY 4.0
