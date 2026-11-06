"""
Etch Assent MCP server — lets an agent ask a human for a signature with a provable receipt.

Run over stdio (the client launches it):

    etch-assent-mcp                    # or: python -m etch.assent_mcp

Claude Code:

    claude mcp add etch-assent -- /path/to/.venv/bin/etch-assent-mcp

Encryption happens in this process, on the agent's machine. Etch receives ciphertext and a
SHA-256, never the document or the key. All logic lives in :mod:`etch.assent_agent`; this file
only exposes it as tools.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import assent_agent as core
from .assent_agent import MAX_PLAINTEXT_BYTES, AssentError
from .assent_agent import request_signature as _request_signature

INSTRUCTIONS = """\
Etch Assent asks a human to sign a document or approve an action, and records it on a
tamper-evident chain. Use it when an action needs a person's sign-off you can prove later:
payments, contracts, deploys, consent.

request_signature returns a sign_url. Deliver it to the signer yourself, privately (direct
message or email to that person). The link is a bearer secret: it holds the decryption key and the
right to sign, so never post it in a public channel or a log. Etch cannot read the document.

signature_status shows progress: sent, opened, signing, finalized. Once it reads finalized,
fetch_signed downloads the signed PDF, decrypts it on this machine, and accepts it only if its hash
matches the one on the chain.
"""

server = MCPServer(name="etch-assent", title="Etch Assent", instructions=INSTRUCTIONS,
                   website_url="https://assent.to")


@server.tool(
    title="Request a signature",
    description=(
        "Ask a human to sign. Give `text` (plus an optional `title`) to send a one-page sign-off "
        "document generated from it, or `pdf_path` to send an existing PDF (max 10 MB). The document is "
        "end-to-end encrypted on this machine before upload. Returns `sign_url`, which you must send to "
        "the signer privately; when they open it they review, sign in their browser, and the signed copy "
        "returns encrypted. Also returns `document_id` (keep it to check status) and `verify_url` "
        "(public, safe to share: shows the chain of events, not the document)."
    ),
)
async def request_signature(
    text: str | None = None,
    title: str | None = None,
    pdf_path: str | None = None,
    signer: str | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    """
    Args:
        text: What the human is agreeing to, in plain language. Paragraphs separated by blank lines.
        title: Heading for a text request, e.g. "Approve vendor payment". Defaults to "Approval request".
        pdf_path: Absolute path to an existing PDF to send instead of text.
        signer: Who should sign. Printed on the generated document's Name line; for your records.
        requested_by: Who is asking, e.g. "Claude on behalf of Alex". Printed under the title.
    """
    try:
        return await _request(text, title, pdf_path, signer, requested_by)
    except AssentError as exc:
        # ToolError carries the message to the model; any other exception reaches it as a bare
        # "Error executing tool", which gives the agent nothing to correct.
        raise ToolError(str(exc)) from exc


async def _request(text, title, pdf_path, signer, requested_by) -> dict[str, Any]:
    if (text is None) == (pdf_path is None):
        raise AssentError("give exactly one of `text` or `pdf_path`")
    pdf = None
    if pdf_path is not None:
        path = Path(pdf_path).expanduser()
        if not path.is_file():
            raise AssentError(f"no file at {path}")
        if path.stat().st_size > MAX_PLAINTEXT_BYTES:
            raise AssentError(f"{path.name} is over the 10 MB limit")
        pdf = path.read_bytes()
    req = await _request_signature(pdf=pdf, title=title, text=text, signer=signer, requested_by=requested_by)
    return {
        **req.public(),
        "next_step": "Send sign_url to the signer through a private channel. Anyone holding it can open and "
                     "sign the document.",
    }


@server.tool(
    title="Check signature status",
    description=(
        "Where a signature request stands, read from the public Etch chain: `state` is sent (link not opened "
        "yet), opened, signing, or finalized (signed; call fetch_signed). Also reports `opened_copy_matches_sent`: "
        "false means the signer was shown different bytes from what you sent, so treat the request as compromised. "
        "Signer names and emails in `signatures` are typed by the signer, not verified."
    ),
)
async def signature_status(document_id: str) -> dict[str, Any]:
    """
    Args:
        document_id: The `document_id` returned by request_signature.
    """
    try:
        return await core.signature_status(document_id)
    except AssentError as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    title="Fetch the signed document",
    description=(
        "Download the signed PDF for a finalized request, decrypt it locally, and save it only if its SHA-256 equals "
        "the `finalized` hash on the chain. Returns the saved `path`, the hash, signing times, and an inclusion "
        "proof checked locally: `proof.verified` true means the chain commits to exactly this PDF, provable "
        "without trusting Etch. Only works for requests sent from this machine, since the key is stored here. "
        "Errors if the request is not finalized yet."
    ),
)
async def fetch_signed(document_id: str, output_path: str | None = None) -> dict[str, Any]:
    """
    Args:
        document_id: The `document_id` returned by request_signature.
        output_path: Where to write the PDF. Defaults to ~/.etch/assent/signed/{document_id}.pdf.
            An existing file with different content is never overwritten.
    """
    try:
        return await core.fetch_signed(document_id, output_path=output_path)
    except AssentError as exc:
        raise ToolError(str(exc)) from exc


def main() -> None:
    # httpx logs every request at INFO; on stdio that floods the client's server log with no value.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server.run("stdio")


if __name__ == "__main__":
    main()
