"""
Etch glyph API — sigil generation + public resolver.

Endpoints:
    POST /v1/proof/{proof_id}/glyph    - Render the bar sigil + magic URL bundle.
    GET  /g/{shortcode}                 - Public resolver page (HTML).
    GET  /g/{shortcode}.json            - Resolver as JSON (for SDKs).
    GET  /g/{shortcode}.png             - Bar sigil PNG (for hot-linking).

Design:
  - Shortcode = 8-char Crockford base32 over (proof_id, checksum-of-content_hash).
  - The resolver looks up the proof_id, verifies the embedded checksum against the
    stored content_hash, and refuses to resolve if mismatched (typo / fabrication).
  - The HTML page is intentionally lightweight: server-rendered, no JS frameworks,
    drag-drop client-side hash via the SubtleCrypto API.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import get_session
from .glyph import (
    decode_shortcode,
    encode_shortcode,
    format_shortcode,
    render_bar_sigil,
    verify_shortcode,
)
from .models import ProofRecord

logger = logging.getLogger(__name__)

glyph_router = APIRouter(tags=["Etch Glyph"])


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------

class GlyphBundle(BaseModel):
    proof_id: int
    shortcode: str = Field(description="8-char Crockford base32 (no dashes)")
    pretty: str = Field(description="Pretty-printed shortcode (ABCD-EFGH)")
    magic_url: str = Field(description="Public resolver URL")
    sigil_png_url: str = Field(description="URL to fetch the bar sigil PNG")


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

async def _load_record(proof_id: int) -> Optional[ProofRecord]:
    try:
        async with get_session() as session:
            result = await session.execute(
                select(ProofRecord).where(ProofRecord.leaf_index == proof_id)
            )
            return result.scalar_one_or_none()
    except Exception as exc:
        logger.warning(f"[Etch glyph] DB lookup failed: {exc}")
        return None


def _base_url(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


# ---------------------------------------------------------------------------
# Sigil generation (authenticated by proof_id ownership in real deployments;
# left open here to mirror existing /v1/proof/{id} GET semantics)
# ---------------------------------------------------------------------------

@glyph_router.post(
    "/v1/proof/{proof_id}/glyph",
    summary="Generate the sigil + magic URL bundle for a proof",
)
async def create_glyph(proof_id: int, request: Request) -> GlyphBundle:
    record = await _load_record(proof_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Proof {proof_id} not found")

    shortcode = encode_shortcode(record.leaf_index, record.content_hash)
    base = _base_url(request)
    return GlyphBundle(
        proof_id=record.leaf_index,
        shortcode=shortcode,
        pretty=format_shortcode(shortcode),
        magic_url=f"{base}/g/{shortcode}",
        sigil_png_url=f"{base}/g/{shortcode}.png",
    )


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------

async def _resolve(shortcode: str) -> ProofRecord:
    """Decode + look up + checksum-verify. Raises HTTPException on any failure."""
    try:
        proof_id, _ = decode_shortcode(shortcode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid shortcode: {exc}")

    record = await _load_record(proof_id)
    if not record:
        raise HTTPException(status_code=404, detail="Shortcode does not resolve")

    if not verify_shortcode(shortcode, record.content_hash):
        # Checksum mismatch — typo, or someone fabricating a shortcode that
        # happens to point at an existing proof_id but with a hash they don't know.
        raise HTTPException(status_code=404, detail="Shortcode checksum mismatch")
    return record


@glyph_router.get("/g/{shortcode}.png", summary="Bar sigil PNG")
async def get_sigil_png(shortcode: str) -> Response:
    # We don't strictly need to resolve to render a sigil — the sigil is a pure
    # function of the shortcode. But we resolve anyway so a bogus shortcode
    # returns 404 rather than a misleading "valid-looking" image.
    await _resolve(shortcode)
    png = render_bar_sigil(shortcode)
    return Response(content=png, media_type="image/png")


@glyph_router.get("/g/{shortcode}.json", summary="Resolver result as JSON")
async def get_resolver_json(shortcode: str) -> JSONResponse:
    record = await _resolve(shortcode)
    ts = record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else record.created_at
    return JSONResponse({
        "proof_id": record.leaf_index,
        "shortcode": shortcode.upper().replace("-", ""),
        "content_hash": record.content_hash,
        "label": record.label,
        "owner": record.owner,
        "registered_at": ts,
        "leaf_hash": record.leaf_hash,
        "mmr_root": record.mmr_root,
        "chain_depth": record.leaf_count,
    })


_RESOLVER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Etch · {pretty}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 system-ui, -apple-system, sans-serif; max-width: 640px;
          margin: 4rem auto; padding: 0 1.25rem; color: #1a1a1d; background: #fafafa; }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #ececec; background: #14141a; }}
    code, .box {{ background: #1f1f27 !important; border-color: #2a2a35 !important; }}
  }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; letter-spacing: -0.01em; }}
  .sub {{ color: #888; font-size: 0.92rem; margin: 0 0 1.75rem; }}
  .sigil {{ display: block; max-width: 100%; margin: 0 0 2rem; image-rendering: crisp-edges; }}
  dl {{ display: grid; grid-template-columns: 9rem 1fr; gap: 0.45rem 1rem; margin: 0 0 2rem; }}
  dt {{ color: #888; font-size: 0.88rem; }}
  dd {{ margin: 0; word-break: break-all; }}
  code {{ font: 13px ui-monospace, SFMono-Regular, Menlo, monospace;
          background: #efeff3; padding: 0.1rem 0.35rem; border-radius: 4px; }}
  .box {{ background: #efeff3; border: 1px dashed #ccc; border-radius: 8px;
          padding: 1.25rem; text-align: center; margin: 1rem 0 0; }}
  .box.drag {{ border-color: #555; background: #e3e3eb; }}
  .verdict {{ margin-top: 1rem; font-weight: 600; }}
  .ok {{ color: #1a7f1a; }}
  .bad {{ color: #b22222; }}
  footer {{ margin: 3rem 0 0; color: #999; font-size: 0.82rem; }}
</style>
</head>
<body>
<h1>Etch · {pretty}</h1>
<p class="sub">Content provenance receipt</p>
<img class="sigil" src="/g/{shortcode}.png" alt="Etch sigil for {pretty}" />

<dl>
  <dt>Label</dt><dd>{label}</dd>
  <dt>Owner</dt><dd>{owner}</dd>
  <dt>Registered</dt><dd>{registered_at_iso} <span style="color:#888">({registered_at_unix})</span></dd>
  <dt>Content hash</dt><dd><code>{content_hash}</code></dd>
  <dt>Proof ID</dt><dd>{proof_id}</dd>
  <dt>Chain root</dt><dd><code>{mmr_root_short}…</code></dd>
</dl>

<h2 style="font-size:1.1rem;margin:2rem 0 0.5rem">Verify a copy</h2>
<p class="sub" style="margin:0 0 0.5rem">
  Drop a file here. Hashing happens locally in your browser — the file never leaves your device.
</p>
<div class="box" id="drop">
  <input type="file" id="file" />
  <div class="verdict" id="verdict"></div>
</div>

<footer>Powered by <a href="/" style="color:inherit">Etch</a> · tamper-evident Merkle chain</footer>

<script>
const target = "{content_hash}";
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const verdict = document.getElementById('verdict');

['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => {{
  e.preventDefault(); drop.classList.add('drag');
}}));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => {{
  e.preventDefault(); drop.classList.remove('drag');
}}));
drop.addEventListener('drop', e => {{
  if (e.dataTransfer.files[0]) handle(e.dataTransfer.files[0]);
}});
fileInput.addEventListener('change', e => {{
  if (e.target.files[0]) handle(e.target.files[0]);
}});

async function handle(file) {{
  verdict.textContent = 'Hashing ' + file.name + '…';
  verdict.className = 'verdict';
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', buf);
  const hex = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2,'0')).join('');
  if (hex === target) {{
    verdict.textContent = '✓ Match — this file is the registered content.';
    verdict.className = 'verdict ok';
  }} else {{
    verdict.textContent = '✗ No match. SHA-256: ' + hex.slice(0,16) + '…';
    verdict.className = 'verdict bad';
  }}
}}
</script>
</body>
</html>
"""


@glyph_router.get("/g/{shortcode}", response_class=HTMLResponse, summary="Public resolver page")
async def resolver_page(shortcode: str) -> HTMLResponse:
    record = await _resolve(shortcode)
    import datetime as _dt

    ts = record.created_at.timestamp() if hasattr(record.created_at, "timestamp") else record.created_at
    iso = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    html = _RESOLVER_HTML.format(
        shortcode=shortcode.upper().replace("-", ""),
        pretty=format_shortcode(shortcode),
        label=(record.label or "—"),
        owner=(record.owner or "—"),
        registered_at_iso=iso,
        registered_at_unix=f"{ts:.0f}",
        content_hash=record.content_hash,
        proof_id=record.leaf_index,
        mmr_root_short=record.mmr_root[:16],
    )
    return HTMLResponse(content=html)
