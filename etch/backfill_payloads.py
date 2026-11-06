"""
Backfill ``payload_json`` for records written before it was stored.

Every chain payload carried ``registered_at = time.time()``, read a few microseconds before the chain
stamped its own ``created_at`` (which *is* stored, as ``created_at_exact``). Every other payload field
is in the row. So the payload can be rebuilt by walking the representable doubles just below
``created_at`` until ``sha256(canonical_payload(...))`` equals the stored ``payload_hash``. A match is
a SHA-256 preimage, so a recovered payload proves exactly as much as one stored at write time.

Measured on production (2026-11-03, 431 rows): all recovered, median 11 µs before ``created_at``,
worst 8.6 ms (36K doubles). The default window is 1 s back (``--window`` to widen).

    python -m etch.backfill_payloads --dry-run     # report only
    python -m etch.backfill_payloads               # write payload_json

Only rows with ``payload_json IS NULL`` are touched, and only that column is written. The chain is
never modified. Safe to rerun.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update

from .chain import canonical_payload
from .db import create_tables, get_session
from .models import ProofRecord, RecordEntry

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_BACK = 1.0  # ~100x the worst gap measured on production
NEAR_WINDOW = 0.01
DEFAULT_WINDOW_FORWARD = 0.05  # wall clock can step backwards; look a little ahead too
BATCH = 1000


def recover_payload(base: dict[str, Any], payload_hash: str, created_at: float,
                    back: float = DEFAULT_WINDOW_BACK, forward: float = DEFAULT_WINDOW_FORWARD) -> str | None:
    """Return the canonical payload JSON that hashes to ``payload_hash``, or None if not in the window.

    Searched near-first: the last 10 ms (nearly every row), then ``forward`` ahead (clock steps), then
    the rest of ``back``. A miss costs the whole window, ~4.2M hashes per second of window."""
    def scan(start: float, stop: float, direction: float) -> str | None:
        ts = start
        while (ts >= stop) if direction < 0 else (ts <= stop):
            text = canonical_payload({**base, "registered_at": ts})
            if hashlib.sha256(text.encode()).hexdigest() == payload_hash:
                return text
            ts = math.nextafter(ts, direction)
        return None

    near = created_at - min(NEAR_WINDOW, back)
    return (scan(created_at, near, -math.inf)
            or scan(math.nextafter(created_at, math.inf), created_at + forward, math.inf)
            or scan(math.nextafter(near, -math.inf), created_at - back, -math.inf))


def record_base(row: RecordEntry) -> dict[str, Any]:
    # records_api, assent_api and dooh_api all build this shape.
    return {
        "record_hash": row.record_hash,
        "record_type": row.record_type or "",
        "external_id": row.external_id or "",
        "namespace": row.namespace_id,
    }


def proof_base(row: ProofRecord) -> dict[str, Any] | None:
    if row.action_type == "content_proof":
        return {"content_hash": row.content_hash, "label": row.label or "", "owner": row.owner or ""}
    if row.action_type == "c2pa_manifest" and row.proof_json:
        # proof_json is the canonical manifest dump, the same bytes _manifest_hash hashes.
        m = json.loads(row.proof_json)
        claim = m.get("claim") or {}
        return {
            "manifest_hash": hashlib.sha256(row.proof_json.encode()).hexdigest(),
            "content_hash": m.get("content_hash"),
            "claim_generator": claim.get("claim_generator"),
            "assertion_count": len(claim.get("assertions") or []),
            "assertion_labels": [a.get("label") for a in claim.get("assertions") or []],
            "ingredient_count": len(m.get("ingredients") or []),
            "title": m.get("title") or "",
            "format": m.get("format") or "",
        }
    return None


@dataclass
class Report:
    scanned: int = 0
    recovered: int = 0
    unrecovered: list[str] = field(default_factory=list)

    def line(self, table: str) -> str:
        tail = f", unrecovered: {self.unrecovered[:20]}" if self.unrecovered else ""
        return f"{table}: {self.recovered}/{self.scanned} recovered{tail}"


async def _backfill(model, base_fn, label_fn, *, dry_run: bool, back: float, limit: int | None) -> Report:
    report = Report()
    last_id = 0
    while limit is None or report.scanned < limit:
        size = BATCH if limit is None else min(BATCH, limit - report.scanned)
        async with get_session() as session:
            rows = (await session.execute(
                select(model).where(model.payload_json.is_(None), model.id > last_id).order_by(model.id).limit(size)
            )).scalars().all()
        if not rows:
            break
        last_id = rows[-1].id
        updates = []
        for row in rows:
            report.scanned += 1
            base = base_fn(row)
            found = None
            if base is not None and row.created_at_exact is not None:
                found = recover_payload(base, row.payload_hash, row.created_at_exact, back=back)
            if found is None:
                report.unrecovered.append(label_fn(row))
            else:
                report.recovered += 1
                updates.append({"id": row.id, "payload_json": found})
        if updates and not dry_run:
            async with get_session() as session:
                for u in updates:
                    # The NULL guard keeps a concurrent writer's value; nothing is overwritten.
                    await session.execute(
                        update(model).where(model.id == u["id"], model.payload_json.is_(None))
                        .values(payload_json=u["payload_json"]))
        logger.info(f"{model.__tablename__}: {report.recovered}/{report.scanned}")
    return report


async def backfill(*, dry_run: bool = False, back: float = DEFAULT_WINDOW_BACK,
                   limit: int | None = None) -> dict[str, Report]:
    await create_tables()  # adds the payload_json columns if this database predates them
    return {
        "etch_records": await _backfill(RecordEntry, record_base, lambda r: r.record_id,
                                        dry_run=dry_run, back=back, limit=limit),
        "etch_proofs": await _backfill(ProofRecord, proof_base, lambda r: f"proof:{r.leaf_index}",
                                       dry_run=dry_run, back=back, limit=limit),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="recover and report, write nothing")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW_BACK, help="seconds to search back")
    parser.add_argument("--limit", type=int, default=None, help="max rows per table")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    started = time.time()
    reports = asyncio.run(backfill(dry_run=args.dry_run, back=args.window, limit=args.limit))
    for table, report in reports.items():
        print(report.line(table))
    print(f"{'dry run, nothing written' if args.dry_run else 'written'} in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
