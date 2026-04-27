"""
Etch glyph — shortcodes + visual sigils.

A shortcode is a compact, human-typeable identifier for a proof record. It
encodes the proof_id (32 bits) plus an 8-bit checksum derived from the proof's
content_hash, so a typo or fabrication is detectable without a DB lookup.

Shortcode wire format (40 bits → 8 chars Crockford base32):
    [ 32-bit proof_id ][ 8-bit checksum ]

The checksum is the first byte of SHA-256(content_hash || proof_id_be4).

Visuals:
    render_bar_sigil(shortcode) -> PNG bytes — Spotify-code-style horizontal bars.
    render_qr_sigil(shortcode, base_url) -> PNG bytes — standard QR for video corners.
"""
from __future__ import annotations

import hashlib
import io
import struct

from PIL import Image, ImageDraw

# Crockford base32 alphabet — no I/L/O/U so OCR and humans don't confuse chars.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_INV = {c: i for i, c in enumerate(_CROCKFORD)}


def _checksum(proof_id: int, content_hash: str) -> int:
    """First byte of SHA-256(content_hash_bytes || proof_id_be4) — 0..255."""
    h = hashlib.sha256()
    h.update(bytes.fromhex(content_hash))
    h.update(struct.pack(">I", proof_id & 0xFFFFFFFF))
    return h.digest()[0]


def encode_shortcode(proof_id: int, content_hash: str) -> str:
    """Encode a proof_id + content_hash into an 8-char Crockford base32 shortcode."""
    if proof_id < 0 or proof_id > 0xFFFFFFFF:
        raise ValueError("proof_id must fit in 32 bits")
    if len(content_hash) != 64:
        raise ValueError("content_hash must be 64 hex chars")

    cksum = _checksum(proof_id, content_hash)
    payload = (proof_id << 8) | cksum  # 40 bits

    chars = []
    for i in range(8):
        shift = (7 - i) * 5
        chars.append(_CROCKFORD[(payload >> shift) & 0x1F])
    return "".join(chars)


def decode_shortcode(shortcode: str) -> tuple[int, int]:
    """Decode a shortcode back to (proof_id, checksum). Does not verify checksum."""
    s = shortcode.strip().upper().replace("-", "")
    # Crockford forgiving substitutions
    s = s.replace("I", "1").replace("L", "1").replace("O", "0")
    if len(s) != 8:
        raise ValueError(f"shortcode must be 8 chars (got {len(s)})")

    payload = 0
    for c in s:
        if c not in _CROCKFORD_INV:
            raise ValueError(f"invalid character {c!r} in shortcode")
        payload = (payload << 5) | _CROCKFORD_INV[c]

    proof_id = payload >> 8
    cksum = payload & 0xFF
    return proof_id, cksum


def verify_shortcode(shortcode: str, content_hash: str) -> bool:
    """True iff the shortcode's embedded checksum matches the content_hash."""
    try:
        proof_id, cksum = decode_shortcode(shortcode)
    except ValueError:
        return False
    return _checksum(proof_id, content_hash) == cksum


def format_shortcode(shortcode: str) -> str:
    """Pretty-print: ABCD-EFGH (groups of 4) — purely cosmetic."""
    s = shortcode.strip().upper().replace("-", "")
    return f"{s[:4]}-{s[4:]}" if len(s) == 8 else s


# ---------------------------------------------------------------------------
# Visual sigils
# ---------------------------------------------------------------------------

def render_bar_sigil(
    shortcode: str,
    width: int = 480,
    height: int = 120,
    fg: tuple[int, int, int] = (20, 20, 24),
    bg: tuple[int, int, int] = (245, 245, 248),
) -> bytes:
    """
    Render a Spotify-code-style horizontal bar glyph for a shortcode.

    Eight bars, one per char, with height proportional to the char's 5-bit value.
    Compact (480x120 default), prints well in album art and video stills.
    """
    s = shortcode.strip().upper().replace("-", "")
    if len(s) != 8:
        raise ValueError("shortcode must be 8 chars after stripping dashes")

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    margin_x = width // 16
    margin_y_top = height // 6
    label_h = height // 5
    bar_area_h = height - margin_y_top - label_h - margin_y_top // 2

    n = 8
    bar_w = max(2, (width - 2 * margin_x) // (n * 2))
    gap = bar_w
    total_w = n * bar_w + (n - 1) * gap
    start_x = (width - total_w) // 2

    for i, c in enumerate(s):
        v = _CROCKFORD_INV[c]  # 0..31
        # Map 5-bit value to bar height: minimum 20% of bar area, max 100%.
        h_frac = 0.2 + (v / 31.0) * 0.8
        bar_h = int(bar_area_h * h_frac)
        x0 = start_x + i * (bar_w + gap)
        y0 = margin_y_top + (bar_area_h - bar_h)
        x1 = x0 + bar_w
        y1 = margin_y_top + bar_area_h
        draw.rectangle([x0, y0, x1, y1], fill=fg)

    # Label below the bars: "ETCH ABCD-EFGH"
    label = f"ETCH  {format_shortcode(s)}"
    try:
        # Built-in default font; size depends on Pillow version but it's readable.
        bbox = draw.textbbox((0, 0), label)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:
        tw, th = len(label) * 6, 11
    tx = (width - tw) // 2
    ty = margin_y_top + bar_area_h + (label_h - th) // 2
    draw.text((tx, ty), label, fill=fg)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_qr_sigil(shortcode: str, base_url: str, size: int = 400) -> bytes:
    """
    Render a QR code pointing at {base_url}/g/{shortcode}.

    Falls back gracefully if the optional `qrcode` lib isn't installed —
    raises ImportError with an actionable message.
    """
    try:
        import qrcode  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "render_qr_sigil requires the 'qrcode' package — `pip install qrcode[pil]`"
        ) from exc

    s = shortcode.strip().upper().replace("-", "")
    url = f"{base_url.rstrip('/')}/g/{s}"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    if img.size[0] != size:
        img = img.resize((size, size), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
