"""DOOH reference player — minimal demo of the etch.dooh SDK.

This script simulates a digital signage player running on a screen. For each
"play" of a creative it:

  1. Builds a canonical Receipt body.
  2. Signs it with the player key (player_sig).
  3. Countersigns with the venue key (venue_countersig).
  4. Anchors the signed bundle in an Etch namespace.
  5. Saves the bundle to disk for delivery to the advertiser.

In production the player and venue keys live on separate processes (or
hardware): the player asks the venue's countersigner over a local network or
back-channel before anchoring. This reference collapses both into one process
so the demo runs end-to-end on a single host.

Usage:
    python examples/dooh_reference_player.py \\
        --etch-url http://localhost:8100 \\
        --etch-key etch_live_sk_... \\
        --campaign Q2-2026-launch \\
        --plays 25 \\
        --out ./bundles/

You'll also need to bootstrap a namespace and create the keys + manifest the
first time. Pass --setup to do that automatically into ./player-state/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from etch.dooh import (
    EtchSubmitter,
    IdentityManifest,
    KeyPair,
    Receipt,
    countersign,
    sign_receipt,
)
from etch.dooh.receipt import Geo, attach_proof, build_unsigned_bundle, bundle_to_dict

# Demo creatives — would normally be hashes of real video/image files.
DEMO_CREATIVES = [
    ("creative-001-launch-15s",  15_000, "sha256:" + "1" * 64),
    ("creative-002-feature-30s", 30_000, "sha256:" + "2" * 64),
    ("creative-003-cta-10s",     10_000, "sha256:" + "3" * 64),
]


def now_iso_ms() -> str:
    """RFC 3339 UTC, millisecond precision (e.g. '2026-04-27T18:42:13.000Z')."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def setup_state(state_dir: Path, advertiser_id: str, screen_id: str, venue_id: str) -> tuple[KeyPair, KeyPair, IdentityManifest, Path]:
    """Create or load player key, venue key, manifest, sequence counter file."""
    state_dir.mkdir(parents=True, exist_ok=True)

    player_key_path = state_dir / "player.key.json"
    venue_key_path = state_dir / "venue.key.json"
    manifest_path = state_dir / "manifest.json"
    seq_path = state_dir / "sequence"

    if player_key_path.exists():
        player = KeyPair.load(player_key_path)
    else:
        player = KeyPair.generate(f"{screen_id}#k1")
        player.save(player_key_path)
        print(f"[setup] generated player key → {player_key_path}")

    if venue_key_path.exists():
        venue = KeyPair.load(venue_key_path)
    else:
        venue = KeyPair.generate(f"{venue_id}#k1")
        venue.save(venue_key_path)
        print(f"[setup] generated venue key → {venue_key_path}")

    if manifest_path.exists():
        manifest = IdentityManifest.load(manifest_path)
    else:
        manifest = IdentityManifest(advertiser_id=advertiser_id, issued_at=now_iso_ms())
        manifest.add_screen(player.key_id, player.public_b64)
        manifest.add_venue(venue.key_id, venue.public_b64)
        manifest.save(manifest_path)
        print(f"[setup] wrote manifest → {manifest_path}")

    if not seq_path.exists():
        seq_path.write_text("0")

    return player, venue, manifest, seq_path


def next_sequence(seq_path: Path) -> int:
    n = int(seq_path.read_text().strip() or 0)
    seq_path.write_text(str(n + 1))
    return n


def play_one(
    *,
    creative_id: str,
    duration_ms: int,
    creative_hash: str,
    advertiser_id: str,
    campaign_id: str,
    screen_id: str,
    venue_id: str,
    player_key: KeyPair,
    venue_key: KeyPair,
    submitter: EtchSubmitter,
    sequence: int,
    out_dir: Path,
    fast: bool,
) -> dict:
    """Render a creative, sign, anchor, persist. Return a small summary dict."""
    print(f"[play] {creative_id} ({duration_ms} ms) seq={sequence}")
    if not fast:
        time.sleep(duration_ms / 1000.0)
    else:
        time.sleep(0.05)  # token sleep so sequencing feels real

    receipt = Receipt(
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        creative_hash=creative_hash,
        duration_ms=duration_ms,
        played_at=now_iso_ms(),
        screen_id=screen_id,
        sequence=sequence,
        venue_id=venue_id,
        geo=Geo(lat=40.758, lon=-73.985),
    )

    player_sig = sign_receipt(receipt, player_key)
    # — In production this would be an RPC to a venue countersigner —
    venue_sig = countersign(receipt, venue_key)

    bundle = build_unsigned_bundle(receipt, player_sig, venue_sig)
    proof = submitter.submit(bundle)
    bundle = attach_proof(bundle, proof)

    out_path = out_dir / f"{campaign_id}-{screen_id.replace(':', '_')}-{sequence:06d}.bundle.json"
    out_path.write_text(json.dumps(bundle_to_dict(bundle), indent=2, sort_keys=True))

    return {
        "sequence": sequence,
        "creative": creative_id,
        "leaf_index": proof.leaf_index,
        "mmr_root": proof.mmr_root[:16] + "...",
        "bundle_path": str(out_path),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--etch-url", default="http://localhost:8100")
    ap.add_argument("--etch-key", required=True, help="Etch namespace API key")
    ap.add_argument("--advertiser-id", default="advertiser:demo")
    ap.add_argument("--campaign", default="demo-campaign-001")
    ap.add_argument("--screen-id", default="screen:demo-01")
    ap.add_argument("--venue-id", default="venue:demo")
    ap.add_argument("--plays", type=int, default=10, help="Number of plays before exit")
    ap.add_argument("--state-dir", type=Path, default=Path("./player-state"))
    ap.add_argument("--out", type=Path, default=Path("./bundles"))
    ap.add_argument("--fast", action="store_true", help="Skip duration sleep (smoke-test mode)")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    player, venue, _manifest, seq_path = setup_state(
        args.state_dir, args.advertiser_id, args.screen_id, args.venue_id,
    )

    print(f"[start] {args.plays} plays → {args.etch_url} (advertiser={args.advertiser_id})")
    summaries = []
    with EtchSubmitter(api_key=args.etch_key, base_url=args.etch_url) as submitter:
        for i in range(args.plays):
            creative_id, duration_ms, creative_hash = DEMO_CREATIVES[i % len(DEMO_CREATIVES)]
            summary = play_one(
                creative_id=creative_id,
                duration_ms=duration_ms,
                creative_hash=creative_hash,
                advertiser_id=args.advertiser_id,
                campaign_id=args.campaign,
                screen_id=args.screen_id,
                venue_id=args.venue_id,
                player_key=player,
                venue_key=venue,
                submitter=submitter,
                sequence=next_sequence(seq_path),
                out_dir=args.out,
                fast=args.fast,
            )
            summaries.append(summary)
            print(f"       anchored leaf={summary['leaf_index']} root={summary['mmr_root']}")

    print(f"\n[done] {len(summaries)} plays anchored, bundles written to {args.out}/")
    print(f"       state in {args.state_dir}/ — share manifest.json with the advertiser/auditor for verification")
    return 0


if __name__ == "__main__":
    sys.exit(main())
