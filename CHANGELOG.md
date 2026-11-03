# Changelog

All notable changes to Etch are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything below has landed on `main` since the `v0.2.0` tag. `pyproject.toml`
still reads `0.2.0` — bump it before tagging the next release (see
[`docs/RELEASING.md`](docs/RELEASING.md)).

### Added

- **Etch Assent** (`/v1/assent/*`) — client-side PDF signing with an anonymous
  provenance chain. The PDF never reaches the server; only hashes and event
  metadata do. Events are pinned to the `assent/public` namespace and capped at
  32 per document; per IP, 30 new documents and 600 follow-on events an hour.
  - `POST /v1/assent/stamp` — commit an `assent.event`
  - `GET /v1/assent/chain/{document_id}` — event chain with integrity check
  - `GET /v1/assent/records/{record_id}` and `…/proof` — public receipt + inclusion proof
  - `GET /v1/assent/verify?hash=` — find events by document hash
- **Assent send-to-sign (E2EE)** — opaque ciphertext store at
  `/v1/assent/document`. The browser encrypts with AES-256-GCM and keeps the key
  in the URL fragment, which browsers never transmit, so Etch cannot decrypt what
  it holds. 10 uploads per IP per hour, 15 MB cap, disk path via
  `ETCH_ASSENT_DOC_DIR`.
- **Capability token for document replacement** — `PUT /v1/assent/document/{id}`
  now requires the `X-Assent-Write-Token` issued at upload time, so possession of
  a document ID is no longer sufficient to overwrite it. The sender bakes the
  token into the share link's fragment, so the server never sees it in transit.
- **Multi-field signing with a review/finalize gate** — several signature fields
  per document, each independently placed, resized, and dragged; a review stage
  between placing and finalizing.
- **Customizable verification stamp** — the QR verification stamp is draggable,
  resizable, and can be switched off entirely.
- **DOOH playback receipts** (`/v1/dooh/*`) — bilaterally-signed proof that a
  creative ran on a specific screen at a specific time, anchored in an
  advertiser-owned namespace. Ships with an offline verifier, an ed25519 keypair
  and identity-manifest model, an `EtchSubmitter` client, and a reference player.
  Spec: [`docs/dooh-spec.md`](docs/dooh-spec.md) (CC-BY 4.0).
- **Glyph** — 8-character Crockford-base32 shortcodes encoding a 32-bit
  `proof_id` plus an 8-bit checksum over the content hash, so typos and
  fabrications are caught before a database lookup. Adds a bar sigil renderer and
  a server-rendered public resolver at `/g/{shortcode}` (plus `.json` and `.png`).
- **etchmark** — inaudible spread-spectrum audio watermark carrying an Etch
  shortcode, with sync recovery and CRC-12 payload framing.
  `POST /v1/proof/{proof_id}/embed-audio` returns lossless 16-bit PCM WAV;
  `POST /v1/proof/extract-audio` recovers the shortcode and resolves it. WAV and
  FLAC are read natively; MP3/AAC/Opus go through `ffmpeg` when it is on `PATH`.
  The watermark is a *pointer* to a chain record, never a proof in itself.
- **`if_changed` on `POST /v1/records`** — with `record.id` set, Etch looks up the
  latest record for that `(namespace, external_id, record_type)` and, if the
  `record_hash` is unchanged, returns the existing receipt with
  `"deduplicated": true` without appending. Opt-in on purpose: the chain is
  append-only, and re-registering unchanged content is a legitimate timestamped
  re-attestation, so the default must never silently deduplicate.
- **`GET /v1/records/stats`** — per-namespace append vs. dedup counters, so the
  saving from `if_changed` is measurable rather than asserted.
- **Structured logging setup** — uvicorn only configures its own loggers, so
  `etch.*` log lines were being dropped, including the dedup line that is the only
  trace a suppressed write leaves. Level via `ETCH_LOG_LEVEL` (default `INFO`).

- **Assent field detection.** Fields are found when a PDF opens, entirely in the
  browser. Fillable PDFs import their own form fields (text, checkbox, radio,
  signature) at the exact widget boxes with existing values; the finalized PDF
  draws the values and strips the original widgets so no viewer paints an empty
  field over them. Flat PDFs get suggested fields from underscore runs, drawn
  underlines, captions under a line, empty table cells (labelled by the cell to
  their left), checkboxes in every form Word produces — Wingdings / Wingdings 2
  symbols, ☐ glyphs, content controls, legacy form checkboxes, drawn squares —
  plus `( )` and `[ ]` blanks, and bare labels like `Name:`. A box the document
  prints as ☑ comes in checked; unchecking it paints the printed mark out.
  Designed forms (InDesign / government templates — light field boxes on a
  tinted panel, question printed at the top of each box) get a field under
  each question; centered column headers, yes/no-only questions, paragraph
  boxes and "office use only" panels are left alone. A blank repeated at the
  same spot on every page (a running "Applicant name" header) is one value.
  An auto-filled date beside a signature line is kept only if that line was
  signed. Date blanks prefill today; name and email blanks follow
  the identity panel. Detected signature boxes are suggestions: they don't block
  finishing and only reach the chain when signed.
- Assent checkbox fields, placeable by hand as well as detected.

### Changed

- A document can now be finalized on text fields alone; a signature field is no
  longer required.
- The verification stamp shrank from a full-width banner to a corner badge.
- A signed Assent signature field can still be moved and resized while placing;
  the finalized PDF contain-fits the ink into the box instead of stretching it.
- Assent text fields fit their text by default: one line tall, 120pt wide while
  empty, then hugging the typed text as it grows and shrinks (trailing spaces
  trimmed on blur), so a field stops covering the page around it. A manual resize
  pins the box; a **fit** button snaps it back. Fields resize down to one glyph
  by one line, and the finalized text is vertically centered in its box.
- `POST /v1/proof/batch` accepts up to 1000 items per request.

### Fixed

- Assent's rate limit counted every event, so one long form with several
  signature lines used most of an office NAT's hourly budget. The per-IP limit
  is now on new documents (30/hour), with a 600/hour flood guard on follow-on
  events. A refused `created` event no longer leaves the session dead: the
  client retries it before the next write, and a 429 explains itself instead
  of surfacing the raw API detail.
- The drawn-signature pad opens as a modal instead of at the bottom of the
  sidebar, where it was off-screen when signing deep in a long document.
- Drawn signatures are stamped with a transparent background, so the ink no
  longer erases the signature line it's placed on.
- Index `idx_records_ns_ext` on `(namespace_id, external_id, record_type)` keeps
  the `if_changed` lookup flat as external IDs accumulate, instead of degrading
  into a scan.
- Assent field placement is predictable, and a placed signature no longer hides
  text beneath it.
- Assent "remove field" and "remove stamp" work — pressing them no longer starts
  a drag on the field, which had swallowed the click and opened the signature pad.

### Removed

- Internal outreach documents and infrastructure details are no longer committed
  to the public repository (`docs/_private/` is now gitignored).

## [0.2.0] — 2026-03-27

### Added

- **System of Record API** (`/v1/records/*`) — namespace-isolated chains with API
  key authentication. Keys are formatted `etch_{live|test}_sk_{token}` and stored
  only as SHA-256 hashes. Includes cursor pagination, self-contained inclusion
  proofs, and `GET /v1/chain/root`.
- **C2PA compatibility layer** (`/v1/c2pa/*`) — register, retrieve, verify, and
  bridge C2PA manifests against the Etch chain, for EU AI Act provenance work.
- **Batch registration** — `POST /v1/proof/batch`.
- **Landing site** (`site/index.html`) and deployment infrastructure: Dockerfile,
  `docker-compose.yml`, and reverse-proxy configuration.

## [0.1.0] — 2026-03-18

### Added

- Initial extraction of Etch as a standalone project: the MMR audit chain
  (`AuditChain`, `ChainEntry`, `InclusionProof`), offline verification via
  `verify_inclusion_proof()`, and the legacy `/v1/proof/*` registration API.
- Async Python SDK (`EtchClient`).
- CI matrix across Python 3.11/3.12/3.13 with ruff linting, and a release
  workflow publishing to PyPI via OIDC trusted publishing.

[Unreleased]: https://github.com/maco144/Etch/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/maco144/Etch/releases/tag/v0.2.0
