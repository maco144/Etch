# DOOH Playback Receipt — Spec (v0)

**Status:** Draft
**License:** CC-BY 4.0

## Overview

A receipt format and signing protocol for digital out-of-home (DOOH) ad playback. Every time a creative is shown on a screen, the player produces a bilaterally-signed receipt that is anchored in an Etch namespace and verifiable offline by anyone.

The goal is **defensible proof of contracted delivery** — an advertiser can prove a campaign ran on the screens, at the times, and for the durations it paid for, without trusting the venue, the SSP, or us.

### Position

DOOH measurement today is *estimated* (audience modeling, mobile-device proxies, vehicle counts). Incumbents — Vistar, Place Exchange, Broadsign, Hivestack — issue logs the advertiser must trust. There is no cryptographic playback proof in the category.

This spec defines that primitive.

### Non-goals

- Not a CMS. Not a competitor to Yodeck, Xibo, BrightSign.
- Not a media network. We don't sell impressions.
- Not a hardware attestation protocol — see "Trust model" for what this spec does and doesn't claim.

## Trust model — what this protocol does and doesn't prove

This is the spec's most important section. Read it before evaluating the rest.

### What a valid receipt proves

A receipt validates if and only if all of the following hold:

1. The receipt body is signed by an ed25519 key the **advertiser** previously associated with a screen (`player_sig`).
2. The same body is countersigned by an ed25519 key the **advertiser** previously associated with a venue or media owner (`venue_countersig`).
3. The hash of the signed bundle equals the `record_hash` recorded in the bundle's `etch_proof` block.
4. The bundle's `etch_proof.mmr_root` matches a chain root the verifier independently trusts (the advertiser's published namespace root, fetched out-of-band).
5. The Etch chain `timestamp` is at or after `played_at`.

If all five conditions hold, the advertiser has cryptographic proof that:

- **Both parties signed.** The venue cannot later claim the screen never ran the creative; the player cannot fabricate plays the venue refused to countersign.
- **The play happened at or before `played_at`.** The Etch chain timestamp is a server-attested upper bound, so a player lying about its local clock is bounded by when the receipt actually reached the chain.
- **The chain has not been tampered with.** Etch's MMR is append-only; a forged or backdated receipt would not match the trusted namespace root.

Step 4 is what closes the protocol against single-party forgery. Without an independently-trusted root, an attacker who controls both the bundle and its `etch_proof` block could fabricate an internally-consistent proof pointing at a nonexistent leaf. With a trusted root, no such forgery passes. **A verifier that skips step 4 is doing partial verification and should warn loudly.**

### What a valid receipt does NOT prove

**Cooperative fraud is out of scope.** If the venue and the player collude — either because they are the same party, or because they have agreed off-protocol to inflate plays — they can produce arbitrarily many valid receipts for plays that never happened. The protocol stops *adversarial* forgery between *opposed* parties; it does not stop a venue and player who have agreed to lie together.

The mitigation path, in order of strength, is **outside this spec**:

1. **Three-party receipts** — add an SSP or auditor as a third signer. Collusion now requires three parties to coordinate.
2. **Sensor-backed proof-of-display** — camera detecting a pixel pattern, ambient-light sensor, audio fingerprint. Out of v0 scope.
3. **Hardware attestation** — TPM-rooted key generation on the player. The player can no longer issue arbitrary signatures; each one is bound to a measured boot of trusted firmware. This is the long-term answer for high-trust deployments (pharma, political).

We name the limit explicitly because it is the first question any compliance team will ask. The honest answer — *"this protocol stops adversarial forgery; cooperative fraud requires hardware attestation, here's the upgrade path"* — is a stronger sales position than pretending the limit doesn't exist.

### Trust anchors

- **Advertiser** is the trust anchor for screen and venue identity. The advertiser, not the venue or a central registry, decides which ed25519 keys belong to which `screen_id` and `venue_id`. This avoids a global PKI and matches the commercial reality: advertisers contract directly with media owners.
- **Etch** is the trust anchor for ordering and tamper-evidence. Etch is namespace-isolated; one advertiser's chain cannot affect another's.
- **Verifiers** trust only the receipt's cryptography and the Etch chain. They do not trust us, the venue, the player, or any SSP.

## Identity model

### Keys

Three roles, two key types:

| Role | Key type | Lifetime | Issued by |
|---|---|---|---|
| Player (one per screen) | ed25519 | Long-lived; rotated on screen replacement | Advertiser, at install time |
| Venue (one per media owner) | ed25519 | Long-lived; rotated on contract change | Advertiser, at contract signing |
| Advertiser (Etch API key) | Etch API key | Long-lived | Etch namespace bootstrap |

Keys are not derived from any seed protocol in v0. v1 may layer Concord-style deterministic identity on top, but the spec does not require it.

### IDs

- `screen_id`: opaque string chosen by the advertiser (e.g. `screen:nyc-times-sq-07`).
- `venue_id`: opaque string chosen by the advertiser (e.g. `venue:outfront-northeast`).
- `advertiser_id`: opaque string the advertiser uses to identify itself in receipts.
- `campaign_id`: advertiser-issued; meaningful inside their billing/CRM.
- `creative_hash`: `sha256:<hex>` of the creative file bytes. Deterministic; not a UUID.

The advertiser publishes an **identity manifest** — a signed JSON file mapping each `screen_id` and `venue_id` to its current ed25519 public key. Verifiers fetch the manifest from the advertiser to resolve keys at verification time. The manifest is itself anchored in Etch on every change, so historical resolution is possible.

## Receipt schema

A receipt is a single canonical JSON object. Field order is fixed (alphabetical) so the same content always produces the same bytes for hashing.

```json
{
  "advertiser_id": "advertiser:acme",
  "campaign_id": "Q2-2026-launch",
  "creative_hash": "sha256:5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
  "duration_ms": 15000,
  "geo": { "lat": 40.7580, "lon": -73.9855 },
  "played_at": "2026-04-27T18:42:13.000Z",
  "schema_version": "dooh-receipt/1",
  "screen_id": "screen:nyc-times-sq-07",
  "sequence": 14823,
  "venue_id": "venue:outfront-northeast"
}
```

### Field rules

| Field | Type | Required | Notes |
|---|---|---|---|
| `advertiser_id` | string | yes | Matches advertiser's Etch namespace owner |
| `campaign_id` | string | yes | Advertiser-scoped; not globally unique |
| `creative_hash` | string | yes | `sha256:` prefix + 64 hex chars |
| `duration_ms` | integer | yes | Wall-clock playback duration, milliseconds |
| `geo` | object | optional | `{lat, lon}` if available; omit field entirely if not |
| `played_at` | string | yes | RFC 3339 UTC, millisecond precision |
| `schema_version` | string | yes | `dooh-receipt/1` for this spec |
| `screen_id` | string | yes | Advertiser-scoped |
| `sequence` | integer | yes | Monotonic per `(screen_id, advertiser_id)`; lets verifiers detect missing receipts |
| `venue_id` | string | yes | Advertiser-scoped |

### Canonicalization

Before signing, the receipt body is serialized as **JCS** (RFC 8785, JSON Canonicalization Scheme) — UTF-8, no whitespace, sorted keys, deterministic number formatting. This is what gets hashed and signed.

## Signed bundle

The on-the-wire and on-disk format wraps the canonical receipt body with both signatures:

```json
{
  "schema_version": "dooh-receipt-bundle/1",
  "receipt": { ... canonical receipt body ... },
  "player_sig": {
    "alg": "ed25519",
    "key_id": "screen:nyc-times-sq-07#k1",
    "sig": "base64url(sig)"
  },
  "venue_countersig": {
    "alg": "ed25519",
    "key_id": "venue:outfront-northeast#k1",
    "sig": "base64url(sig)"
  },
  "etch_proof": {
    "namespace": "ns_advertiser_acme",
    "record_id": "rec_...",
    "leaf_index": 14823,
    "leaf_hash": "...",
    "mmr_root": "...",
    "timestamp": 1714247413.123
  }
}
```

Both `player_sig.sig` and `venue_countersig.sig` are computed over the **same** canonicalized receipt body bytes — there is no separate countersign payload. This is what makes them "the same statement, signed twice."

## Protocol flow

1. **Play.** Player renders creative; captures `played_at`, `duration_ms`, optional `geo`, increments `sequence`.
2. **Build body.** Player constructs the canonical receipt body (JCS).
3. **Sign.** Player produces `player_sig` over the body bytes.
4. **Countersign.** Player sends body + `player_sig` to the venue's countersigner endpoint over a local network or back-channel; venue verifies the body looks right (creative ran on a screen the venue actually owns, sequence is monotonic) and returns `venue_countersig`.
5. **Anchor.** Player (or a batched submitter) hashes the signed bundle (everything except `etch_proof`) with SHA-256 and submits it to the advertiser's Etch namespace via `POST /v1/records`. Etch returns an inclusion proof.
6. **Persist.** Player stores the full bundle (body + both sigs + `etch_proof`) locally for delivery to the advertiser. Venue may also keep a copy.

If countersigning fails (network, venue offline, venue refuses), the receipt is **not** anchored. There is no single-signed receipt format. This is intentional: a single-signed receipt is not bilaterally provable and would weaken the trust model.

### Batching

Each playback produces one receipt. Receipts are anchored in Etch one-at-a-time **or** in micro-batches of N (default: 1; configurable up to 100). Batching is an SDK detail — the spec describes one receipt per play, but a batched submitter can collect N receipts and submit them as N consecutive `POST /v1/records` calls within one HTTP keep-alive session. Each receipt still gets its own MMR leaf and its own inclusion proof. We do **not** define a Merkle-of-Merkles batch leaf in v0; it adds verifier complexity for marginal throughput gain at the volumes a single screen produces.

## Etch namespace model

**One namespace per advertiser.** The advertiser owns the namespace and the API key. Venues do not write directly to Etch; they hand signed bundles back to the player or to an advertiser-controlled submitter.

This matches the commercial model — advertisers pay for verifiability — and keeps verification queries clean (one namespace = one chain = one query surface per advertiser).

A future variant for SSP-led deployments may use one namespace per SSP with sub-scoping by `advertiser_id`; out of v0 scope.

## Verification

A verifier needs three things to verify a receipt offline:

1. The signed bundle file (with `etch_proof`).
2. The advertiser's identity manifest (to resolve `key_id` → ed25519 public key).
3. A trusted `mmr_root` for the advertiser's namespace, fetched out-of-band (e.g. from the advertiser's published feed or pinned at contract time).

The verifier runs six steps. Step 4 is the only one that requires a trusted root; the rest run on the bundle alone.

1. Resolve `player_sig.key_id` and `venue_countersig.key_id` against the manifest. If either is unknown, **fail**.
2. Verify both ed25519 signatures over the canonical receipt body. If either fails, **fail**.
3. Recompute `bundle_hash` (SHA-256 over body + both sigs) and confirm it equals `etch_proof.record_hash`. If not, **fail**.
4. Verify the Etch MMR inclusion proof: leaf hash, path hashes, and `mmr_root` are mutually consistent (`leaf_hash = SHA256(prev_root : 'record_commit' : payload_hash : timestamp)` and `mmr_root = SHA256(prev_root : leaf_hash)`), AND `mmr_root` matches the trusted namespace root from (3) above. If any sub-check fails, **fail**.
5. Confirm `etch_proof.timestamp` is at or after `played_at`. If the clock claim is later than the chain anchor, **fail**.

If the trusted root from (3) is not provided, the verifier still runs steps 1, 2, 3, 5 and the internal-consistency portion of step 4 — but it must surface a warning that the proof has not been bound to a known chain. The SDK's `verify_bundle()` does exactly this: returns `ok=True` with a warning when `trusted_root` is None, but `ok=False` if it is provided and doesn't match.

A verifier that passes all five steps with `trusted_root` supplied has a complete, third-party-replayable proof that the play happened on a screen the advertiser registered, on a date at or before the chain anchor, in a chain that the advertiser publicly attests to.

## HTTP endpoints (Etch implementation)

The reference Etch implementation exposes three DOOH-specific endpoints. They are conveniences over the generic `/v1/records/*` surface, not part of the protocol — alternative implementations are free to skip them and use plain `/v1/records` for anchoring.

| Endpoint | Purpose |
|---|---|
| `POST /v1/dooh/receipts` | Anchor a `SignedBundle` (without `etch_proof`); store it in record metadata; return the bundle with `etch_proof` attached. |
| `GET  /v1/dooh/receipts` | Query bundles by `campaign_id`, `screen_id`, `played_at_from`, `played_at_to`, with cursor pagination. Returns full `SignedBundle` objects with `etch_proof` populated from chain state. |
| `POST /v1/dooh/verify`   | Run the offline verifier server-side. For callers who don't want to install the Python SDK. |

Two anchoring modes are supported. `POST /v1/dooh/receipts` stores the full bundle (managed verification). For privacy-preserving deployments, `POST /v1/records` with `record_hash` only anchors the hash — the advertiser hosts bundles themselves. Both modes produce the same anchored hash on the same chain.

## Versioning

- `schema_version` strings are mandatory. v0 ships `dooh-receipt/1` and `dooh-receipt-bundle/1`.
- Breaking changes increment the integer (`/2`, `/3`).
- Verifiers MUST refuse unknown versions rather than guess.

## Out of v0 scope (roadmap)

| Capability | Why deferred |
|---|---|
| Hardware attestation (TPM, secure enclave) | Required for cooperative-fraud resistance; vendor-specific; not blocking for first design partner |
| Three-party receipts (SSP/auditor signer) | Useful but doubles negotiation surface; first design partner conversation will tell us if it's mandatory |
| Sensor-backed proof-of-display | Hardware-dependent; per-deployment work |
| Geo attestation beyond raw GPS | Single-source GPS is a known limit; revisit when a partner asks |
| Concord-style deterministic identity | Wedge does not require it; can layer on top in v1 |
| C2PA bridge for AI-generated creative | Etch already has C2PA support; integration is straightforward but not a v0 requirement |

## License (provisional)

- **Spec (this document):** CC-BY 4.0
- **SDK (`etch.dooh` Python module):** Apache-2.0

Standards-track positioning matters for SSP adoption; permissive licenses lower the integration bar.

## Open items for design-partner conversation

The Week-2 engineering deliverables (SDK + reference player + verifier endpoint + 100-receipt demo) are complete. The remaining decisions cannot be answered by code; they need a real partner.

1. **Third-signer requirement.** Does the partner accept bilateral signing, or do they immediately escalate to "we need an SSP/auditor co-signer too"? If the latter, three-party receipts move from roadmap to v1 scope.
2. **Integration story.** The Python reference player is the safest integration path for a first deployment. If the partner runs Xibo, BrightSign, or another CMS, do we build a plugin (PHP for Xibo, JS/native for BrightSign) or ship the Python player as a sidecar process? Defer until we know the partner's stack.
3. **Verifier query surface.** Is `GET /v1/dooh/receipts?campaign_id=&screen_id=&played_at_from=&played_at_to=` the right shape, or does the compliance team need different facets (per-creative, per-venue, per-day rollups)?
4. **Trusted-root distribution.** How does the verifier obtain the trusted `mmr_root`? Options: (a) pinned at contract time, (b) advertiser-published feed, (c) Etch publishes signed roots on a schedule. The choice affects who needs to run what infrastructure.

## References

- Etch repo: `~/etch/` — `etch/chain.py` (MMR), `etch/records_api.py` (namespace API), `etch/c2pa.py` (compliance bridge)
- Strategy doc: `~/codec/strategy/dooh_etch_2026-04-27.md`
- JCS: RFC 8785 — JSON Canonicalization Scheme
- ed25519: RFC 8032
