"""Backfill ``shadow_exit_path_v1`` records from existing laptop data (read-only inputs).

Two sources, both written with the same pure builder the Fly recorder uses:

* ``BACKFILL_SIGNAL_REPLAY``: every closed ``signal_replay_v4`` row in the Fly
  mirror (paper trades and shadow/reversal signal paths), on the row's own
  ticks, exactly as the runtime recorder would have built it. ATR(3m) comes
  from the 1 s tape when the row predates the fill-time ATR stamp.
* ``BACKFILL_TAPE_1S``: one per-signal record per genome AI/cross-venue episode
  on the dense 1 s Bitfinex tape (taker-at-signal REALISTIC_V1 counterfactual,
  at-limit touch at the registry hypothesis offset, and every shadow exit on
  the taker path).

Inputs are never modified or deleted. Outputs go to the analyzer export
directory (outside the repository and outside OneDrive) and are descriptive
archive evidence: the report keeps them out of the headline cohort.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import combo_pathway_config as registry  # noqa: E402
import shadow_exit_paths as sxp  # noqa: E402

DEFAULT_MIRROR = r"C:\DoxxedCrypto\fly-mirror-segments\tree"
DEFAULT_TIER_A = r"C:\DoxxedCrypto\bot-data-compact\tierA"
DEFAULT_OUT_DIR = r"C:\DoxxedCrypto\analyzer-exports\shadow-exits\backfill"
HYPOTHESIS_LIMIT_OFFSET_PCT = 0.30
TAPE_PRE_SEC = 5
TAPE_POST_SEC = 120


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rotations(base: Path) -> list[Path]:
    out = [base] if base.exists() else []
    out += sorted(base.parent.glob(base.name + ".*"), key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0)
    return [p for p in out if p.name == base.name or p.suffix[1:].isdigit()]


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def tape_ticks(tape: Any, start: float, end: float) -> list[tuple[float, float | None, float | None, float | None]]:
    a, b = max(0, int(start) - tape.t0), max(0, min(len(tape.bid), int(end) - tape.t0))
    out = []
    for i in range(a, b):
        bid, ask, last = float(tape.bid[i]), float(tape.ask[i]), float(tape.last[i])
        if math.isnan(bid) or math.isnan(ask):
            continue
        out.append((float(tape.t0 + i), bid, ask, None if math.isnan(last) else last))
    return out


class _AtomicJsonl:
    def __init__(self, path: Path) -> None:
        self.path, self.tmp = path, path.with_suffix(path.suffix + ".tmp")
        self.tmp.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.tmp, "w", encoding="utf-8")
        self.rows = 0

    def write(self, row: dict[str, Any]) -> None:
        self.handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
        self.rows += 1

    def commit(self) -> None:
        self.handle.close()
        os.replace(self.tmp, self.path)


def backfill_signal_replay(mirror: Path, out: _AtomicJsonl, tape: Any = None, limit: int | None = None) -> dict:
    stats = {"rows": 0, "written": 0, "skipped_open": 0, "skipped_duplicate": 0, "skipped_no_path": 0}
    seen: set[str] = set()
    for path in _rotations(mirror / "signal_replay.jsonl"):
        for row in _read_jsonl(path):
            stats["rows"] += 1
            tid = str(row.get("trade_id") or "")
            if row.get("schema") != "signal_replay_v4" or not tid or row.get("dump_reason") != "BUFFER_CLOSED":
                stats["skipped_open"] += 1
                continue
            if tid in seen:
                stats["skipped_duplicate"] += 1
                continue
            seen.add(tid)
            start = sxp._replay_start_ts(row)
            fill_t = _finite(row.get("virtual_fill_t"))
            anchor = (start or 0.0) + (fill_t or 0.0)
            atr = None
            if tape is not None and start is not None:
                from research.genome_grid_study import tape_atr14_pct  # noqa: PLC0415
                atr = tape_atr14_pct(tape, anchor)
            lane = registry.tile_lane_for_trade_id(tid)
            meta = {"research_lane": lane, "atr14_pct_3m": atr,
                    "horizon_sec": sxp.DEFAULT_HORIZON_SEC}
            record = sxp.record_from_replay(row, shadow_set=registry.tile_shadow_exit_set(lane),
                                            source=sxp.SOURCE_BACKFILL_REPLAY, meta=meta)
            if atr is not None:
                record["entry_context"]["atr_source"] = "TAPE_ATR14_3M"
            if record.get("skip_reason"):
                stats["skipped_no_path"] += 1
            out.write(record)
            stats["written"] += 1
            if limit and stats["written"] >= limit:
                return stats
    return stats


def backfill_tape_episodes(mirror: Path, tier_a: Path, out: _AtomicJsonl, tape: Any,
                           limit: int | None = None) -> dict:
    from research.genome_grid_study import load_episodes  # noqa: PLC0415
    episodes, episode_stats = load_episodes(mirror, tape)
    stats = {"episodes": len(episodes), "written": 0, "no_tape": 0, "episode_stats": episode_stats}
    shadow_set = registry.tile_shadow_exit_set(None)
    for ep in episodes:
        ts, price, direction = _finite(ep.get("signal_ts")), _finite(ep.get("signal_price")), ep.get("direction")
        if ts is None or not price or direction not in ("LONG", "SHORT"):
            continue
        ticks = tape_ticks(tape, ts - TAPE_PRE_SEC, ts + sxp.DEFAULT_HORIZON_SEC + TAPE_POST_SEC)
        if not ticks:
            stats["no_tape"] += 1
            continue
        sign = sxp.direction_sign(direction)
        limit_price = price * (1.0 - sign * HYPOTHESIS_LIMIT_OFFSET_PCT / 100.0)
        quote = next((t for t in ticks if t[0] >= ts), None)
        i = int(ts) - tape.t0
        bid_qty = float(tape.bid_qty[i]) if tape.bid_qty is not None and 0 <= i < len(tape.bid_qty) else None
        ask_qty = float(tape.ask_qty[i]) if tape.ask_qty is not None and 0 <= i < len(tape.ask_qty) else None
        record = sxp.build_record(
            source=sxp.SOURCE_BACKFILL_TAPE, trade_id=str(ep.get("episode_id")), direction=direction,
            ticks=ticks, signal_ts=ts, fill_ts=None, entry_price=None, shadow_set=shadow_set,
            lane=str(ep.get("episode_class") or "AI_EPISODE"), limit_price=limit_price,
            atr_pct=ep.get("atr14_pct"),
            context={"regime": ep.get("regime"),
                     "bid": quote[1] if quote else None, "ask": quote[2] if quote else None,
                     "bid_qty": None if bid_qty is None or math.isnan(bid_qty) else bid_qty,
                     "ask_qty": None if ask_qty is None or math.isnan(ask_qty) else ask_qty},
            extra={"decision_id": ep.get("decision_id"), "episode_source": ep.get("source"),
                   "limit_offset_pct": HYPOTHESIS_LIMIT_OFFSET_PCT},
        )
        out.write(record)
        stats["written"] += 1
        if limit and stats["written"] >= limit:
            break
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill shadow-exit path records from laptop data (read-only).")
    parser.add_argument("--mirror", default=os.getenv("SHADOW_EXIT_MIRROR", DEFAULT_MIRROR))
    parser.add_argument("--tier-a", default=os.getenv("SHADOW_EXIT_TIER_A", DEFAULT_TIER_A))
    parser.add_argument("--out-dir", default=os.getenv("SHADOW_EXIT_BACKFILL_DIR", DEFAULT_OUT_DIR))
    parser.add_argument("--skip-tape", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    mirror, out_dir = Path(args.mirror), Path(args.out_dir)
    if "onedrive" in str(out_dir).lower():
        raise SystemExit("SHADOW_EXIT_BACKFILL_ONEDRIVE_FORBIDDEN")
    started = time.time()
    tape = None
    if not args.skip_tape:
        from research.genome_grid_study import load_tape  # noqa: PLC0415
        tape = load_tape(mirror, Path(args.tier_a))
    receipt: dict[str, Any] = {"schema": "shadow_exit_backfill_receipt_v1", "record_schema": sxp.SCHEMA,
                               "mirror": str(mirror), "tier_a": args.tier_a, "inputs_read_only": True,
                               "tape_seconds": None if tape is None else int(len(tape.bid)),
                               "tape_range": None if tape is None else [tape.t0, tape.end]}
    replay_out = _AtomicJsonl(out_dir / "signal_replay_backfill.jsonl")
    receipt["signal_replay"] = backfill_signal_replay(mirror, replay_out, tape, args.limit)
    replay_out.commit()
    if tape is not None:
        tape_out = _AtomicJsonl(out_dir / "tape_episode_backfill.jsonl")
        receipt["tape_episodes"] = backfill_tape_episodes(mirror, Path(args.tier_a), tape_out, tape, args.limit)
        tape_out.commit()
    receipt["build_sec"] = round(time.time() - started, 1)
    receipt["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (out_dir / "backfill_receipt.json").write_text(json.dumps(receipt, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items() if k != "tape_episodes"}, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
