# assent-app — Etch Assent frontend

React SPA for [Etch Assent](../docs/ETCH_ASSENT_SPEC.md): client-side PDF signing
with a public, offline-verifiable provenance chain. The PDF is parsed, signed,
and flattened entirely in the browser — the server sees hashes, event metadata,
and (for send-to-sign) ciphertext it has no key for.

Builds to `../site/assent/`, served by Caddy at `/assent/*`.

## Develop

```bash
npm install
npm run dev        # Vite dev server on :5173, proxies /v1 → localhost:8100
npm run lint       # tsc --noEmit
npm run build      # tsc --noEmit && vite build → ../site/assent/
```

The dev server proxies `/v1` to `http://localhost:8100`, so run the Etch API
alongside it:

```bash
uvicorn etch.server:app --reload --port 8100
```

CI runs `tsc --noEmit` and a full `vite build` on every PR (`.github/workflows/ci.yml`).

**The built bundle is committed.** `site/assent/` is deployed as static files, so
a source change is not live until you rebuild and commit the output — that is what
the `chore(assent): rebuild deployed bundle` commits in the history are.

## Routes

Two path prefixes resolve to the same components, so one bundle serves both
`etch.locker/assent/*` and a bare consumer domain at `/*`. `lib/routing.ts`
(`basePrefix`, `pathFor`, `verifyPath`) resolves which prefix is live; never
hardcode a path.

| Route | Component | Purpose |
|---|---|---|
| `/assent`, `/` | `Home` | Drop a PDF |
| `/assent/sign`, `/sign` | `Sign` | Place fields, sign, review, finalize |
| `/assent/sign/:documentId`, `/sign/:documentId` | `Sign` | Recipient side of send-to-sign |
| `/assent/send`, `/send` | `Send` | Encrypt, upload, build the share link |
| `/verify`, `/verify/:recordOrDocId` | `Verify` | Public verification page |

`Sign` is lazy-loaded: it pulls in PDF.js and pdf-lib (~800 KB minified), and
`Home` and `Verify` should render without paying for that.

## Architecture

```
src/
├── routes/
│   ├── Home.tsx      drop-a-PDF landing
│   ├── Sign.tsx      the signing state machine: placing → review → finalized
│   ├── Send.tsx      send-to-sign: encrypt, upload, produce link + QR
│   └── Verify.tsx    public verify page
├── components/
│   ├── PdfViewer.tsx          PDF.js canvas render, page navigation
│   ├── SignatureField.tsx     a placed signature box
│   ├── SignaturePad.tsx       canvas capture (mouse + touch) → PNG
│   ├── TextFieldOverlay.tsx   printed name, date, etc.
│   ├── StampFieldOverlay.tsx  the QR verification stamp
│   └── VerifyChain.tsx        renders an event chain as a receipt
├── hooks/
│   └── useDraggableResizable.ts   shared drag + resize for every overlay
└── lib/
    ├── etch.ts        browser client for /v1/assent/*
    ├── pdf.ts         pdf-lib writes + PDF.js rendering
    ├── signatures.ts  drawn vs. WebAuthn capture
    ├── crypto.ts      AES-256-GCM, [IV(12) || ciphertext], base64url
    ├── hash.ts        SHA-256 via WebCrypto
    ├── routing.ts     dual-prefix path resolution + fragment parsing
    └── handoff.ts     staging for the dropped PDF, Home → Sign
```

### Things that are the way they are on purpose

**`handoff.ts` uses a module-scoped `Map`, not `sessionStorage`.** Round-tripping
a 10 MB `Uint8Array` through `JSON.stringify` costs roughly 60 MB, which is enough
to kill the tab on mobile Safari. The tradeoff is that a hard refresh between Home
and Sign loses the file, which is recoverable; an OOM is not.

**Secrets live in the URL fragment, never the path or query.** Browsers do not
transmit the fragment, so `#key=<b64>&t=<write_token>` reaches the recipient
without ever reaching the server. This is the whole basis of the claim that Etch
cannot decrypt what it stores — do not move either value into a query parameter,
a header on a GET, or anything that gets logged.

**Every event is stamped before the next one is built.** The chain enforces
`event_index == count of existing events` server-side, so an event emitted out of
order is rejected with a 409 rather than silently landing in the wrong place.

**Blank text fields don't satisfy the finalize gate.** Otherwise a document of
empty boxes would finalize as an unchanged copy of itself and produce a
meaningless receipt.

**The stamp's rect survives being toggled off.** Turning the verification stamp
off and back on restores its last position rather than resetting to default —
toggling is never destructive.

## Related

- [`docs/ETCH_ASSENT_SPEC.md`](../docs/ETCH_ASSENT_SPEC.md) — product + protocol spec
- [`etch/assent_api.py`](../etch/assent_api.py) — event chain endpoints
- [`etch/assent_docs_api.py`](../etch/assent_docs_api.py) — E2EE ciphertext store
- [`deploy/Caddyfile.assent`](../deploy/Caddyfile.assent) — serving config
