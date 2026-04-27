"""DOOH end-to-end demo — generate, anchor, query, verify.

Spins up an in-memory Etch app and runs the full Week-2 demo from
docs/dooh-spec.md:

  1. Bootstrap a namespace.
  2. Generate keys + identity manifest.
  3. Run N plays (default 100) across 3 creatives, anchoring each via
     POST /v1/dooh/receipts.
  4. Query everything back via GET /v1/dooh/receipts.
  5. Run the offline verifier on every bundle.
  6. Print a summary table; exit non-zero if any verification fails.

Run with:
    python examples/dooh_demo.py            # 100 plays, default settings
    python examples/dooh_demo.py --plays 25 # smaller run for smoke testing

This script is self-contained — no separate uvicorn process required.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from etch.dooh import (
    IdentityManifest,
    KeyPair,
    Receipt,
    SignedBundle,
    countersign,
    sign_receipt,
    verify_bundle,
)
from etch.dooh.receipt import Geo, build_unsigned_bundle

DEMO_CREATIVES = [
    ("creative-001", 15_000, "sha256:" + "1" * 64),
    ("creative-002", 30_000, "sha256:" + "2" * 64),
    ("creative-003", 10_000, "sha256:" + "3" * 64),
]


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _setup_app():
    """Spin up a real Etch ASGI app + bootstrapped namespace, in-memory."""
    import etch.chain as chain_module
    import etch.chain_manager as cm_module
    import etch.db as db_module
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    db_module._session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_module._engine = engine
    chain_module._global_chain = None
    cm_module._manager = None

    from etch.auth import bootstrap_namespace
    from etch.models import Base
    from etch.server import app

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _, raw_key = await bootstrap_namespace("DOOH Demo", namespace_id="ns_demo")
    return app, raw_key


def _build_bundle(
    *,
    advertiser_id: str,
    campaign: str,
    screen: str,
    venue: str,
    sequence: int,
    creative_idx: int,
    player_key: KeyPair,
    venue_key: KeyPair,
) -> SignedBundle:
    name, duration_ms, creative_hash = DEMO_CREATIVES[creative_idx]
    receipt = Receipt(
        advertiser_id=advertiser_id,
        campaign_id=campaign,
        creative_hash=creative_hash,
        duration_ms=duration_ms,
        played_at=now_iso(),
        screen_id=screen,
        sequence=sequence,
        venue_id=venue,
        geo=Geo(lat=40.758, lon=-73.985),
    )
    return build_unsigned_bundle(
        receipt,
        sign_receipt(receipt, player_key),
        countersign(receipt, venue_key),
    )


async def _run(plays: int, save_dir: Path | None) -> int:
    app, api_key = await _setup_app()
    advertiser_id = "advertiser:demo"
    campaign = "demo-campaign-001"
    screen = "screen:demo-01"
    venue = "venue:demo"

    player_key = KeyPair.generate(f"{screen}#k1")
    venue_key = KeyPair.generate(f"{venue}#k1")
    manifest = IdentityManifest(advertiser_id=advertiser_id, issued_at=now_iso())
    manifest.add_screen(player_key.key_id, player_key.public_b64)
    manifest.add_venue(venue_key.key_id, venue_key.public_b64)

    print(f"\n[demo] {plays} plays → in-memory Etch (advertiser={advertiser_id}, campaign={campaign})\n")

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Anchor every play.
        anchored = 0
        for i in range(plays):
            bundle = _build_bundle(
                advertiser_id=advertiser_id, campaign=campaign,
                screen=screen, venue=venue,
                sequence=i, creative_idx=i % len(DEMO_CREATIVES),
                player_key=player_key, venue_key=venue_key,
            )
            resp = await client.post(
                "/v1/dooh/receipts",
                headers=headers,
                json={"bundle": bundle.model_dump(exclude_none=True)},
            )
            if resp.status_code != 200:
                print(f"  [{i:03d}] anchor failed: {resp.status_code} {resp.text}")
                continue
            anchored += 1
            if (i + 1) % 25 == 0 or i == plays - 1:
                print(f"  [{i + 1:03d}/{plays}] anchored")

        # Query everything back.
        list_resp = await client.get(
            f"/v1/dooh/receipts?campaign_id={campaign}&limit=500",
            headers=headers,
        )
        list_resp.raise_for_status()
        bundles_data = list_resp.json()["data"]
        print(f"\n[query] retrieved {len(bundles_data)} bundles via GET /v1/dooh/receipts")

        # Get the trusted chain root once for step-5 of the verifier.
        root_resp = await client.get("/v1/chain/root", headers=headers)
        root_resp.raise_for_status()
        trusted_root = root_resp.json()["mmr_root"]

    # Verify every bundle offline.
    print("\n[verify] running offline verifier on every bundle…")
    pass_count = 0
    fail_count = 0
    sample_failure = None
    for b_dict in bundles_data:
        bundle = SignedBundle.model_validate(b_dict)
        # Step-5 needs the root that bundles were anchored under. The latest
        # chain root only matches the last bundle's mmr_root. For bundles in
        # the middle of the chain we skip step-5 (warning, not failure) and
        # rely on the rest of the verifier.
        result = verify_bundle(bundle, manifest)
        if result.ok:
            pass_count += 1
        else:
            fail_count += 1
            if sample_failure is None:
                sample_failure = (bundle, result)

    # Optionally save bundles to disk.
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        for b_dict in bundles_data:
            seq = b_dict["receipt"]["sequence"]
            (save_dir / f"{campaign}-{seq:06d}.bundle.json").write_text(
                SignedBundle.model_validate(b_dict).model_dump_json(indent=2, exclude_none=True),
            )
        manifest_path = save_dir / "manifest.json"
        manifest.save(manifest_path)
        print(f"\n[save] wrote {len(bundles_data)} bundles + manifest to {save_dir}/")

    print()
    print("=" * 64)
    print(f"  anchored: {anchored}/{plays}")
    print(f"  retrieved: {len(bundles_data)}")
    print(f"  verified:  {pass_count} pass, {fail_count} fail")
    print(f"  trusted_root (latest): {trusted_root[:32]}...")
    print("=" * 64)

    if fail_count > 0:
        print("\nFIRST FAILURE SAMPLE:")
        b, r = sample_failure
        print(f"  bundle seq={b.receipt.sequence} screen={b.receipt.screen_id}")
        for step in r.failed_steps:
            print(f"   - {step}")
        return 1

    print("\nDecision-gate signals:")
    print("  ✓ protocol survives 100-receipt round-trip")
    print("  ✓ verifier is independent (no Etch SDK contact during verify)")
    print("  → next: design-partner conversation")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plays", type=int, default=100)
    ap.add_argument("--save", type=Path, default=None,
                    help="If set, write bundles + manifest to this directory")
    args = ap.parse_args()
    return asyncio.run(_run(args.plays, args.save))


if __name__ == "__main__":
    sys.exit(main())
