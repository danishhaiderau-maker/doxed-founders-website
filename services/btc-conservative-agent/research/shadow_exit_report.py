"""Shadow-exit report: side-by-side exit ideas scored on the same recorded paths.

Reads ``shadow_exit_paths.jsonl`` (+ rotations) from the Fly mirror and the
laptop backfill outputs, joins each hold window to ``market_context_1m`` with
the same pure function the schema documents, and aggregates per cohort, tile
and shadow exit: EV per signal with a 1 h cluster-bootstrap CI, win rate with a
Wilson CI, give-back rate (in meaningful profit before the exit, closed
negative) and exit time.

Cohorts never mix: ``LIVE_CURRENT_EPOCH`` (runtime recorder rows) is the only
headline; ``BACKFILL_ARCHIVE`` rows are descriptive archive evidence and can
never enter a current ranking. Every number is REALISTIC_V1 (marketable exits
one observed tick after the trigger, booked at the worse mark).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import shadow_exit_paths as sxp  # noqa: E402

SCHEMA = "shadow_exit_report_v1"
REPORT_NAME = "shadow_exit_report.json"
DEFAULT_MIRROR = r"C:\DoxxedCrypto\fly-mirror-segments\tree"
DEFAULT_OUT_DIR = r"C:\DoxxedCrypto\analyzer-exports\shadow-exits"
COHORT_LIVE = "LIVE_CURRENT_EPOCH"
COHORT_BACKFILL = "BACKFILL_ARCHIVE"
COHORT_ROLES = {COHORT_LIVE: "HEADLINE", COHORT_BACKFILL: "DESCRIPTIVE_ARCHIVE_NOT_RANKABLE"}
REFERENCE_EXITS = (sxp.ACTUAL_EXIT_ID, "hold_to_horizon")
CLUSTER_SEC = 3600
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261003
MAX_RECORDS = 500_000
MAX_RECORD_BYTES = 256 * 1024
MAX_HOLD_WINDOW_SEC = 6 * 3600
_ROTATION = re.compile(r"^shadow_exit_paths\.jsonl(?:\.[1-9][0-9]*)?$")
_MC_ROTATION = re.compile(r"^market_context_1m\.jsonl(?:\.[1-9][0-9]*)?$")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            if len(line) > MAX_RECORD_BYTES or not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def record_sources(mirror: Path | None, backfill_dir: Path | None) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if mirror and mirror.is_dir():
        out += [(COHORT_LIVE, p) for p in sorted(mirror.iterdir()) if _ROTATION.match(p.name)]
    if backfill_dir and backfill_dir.is_dir():
        out += [(COHORT_BACKFILL, p) for p in sorted(backfill_dir.glob("*.jsonl"))]
    return out


def load_records(sources: list[tuple[str, Path]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deduplicated records (by record_id; the latest read wins) with read receipts."""
    by_id: dict[str, dict[str, Any]] = {}
    receipts = []
    for cohort, path in sources:
        n = 0
        for row in _read_jsonl(path):
            if row.get("schema") != sxp.SCHEMA:
                continue
            source = row.get("source")
            if (cohort == COHORT_LIVE) != (source == sxp.SOURCE_RUNTIME):
                continue
            row["_cohort"] = cohort
            by_id[str(row.get("record_id"))] = row
            n += 1
            if len(by_id) >= MAX_RECORDS:
                break
        receipts.append({"cohort": cohort, "file": path.name, "rows": n})
    return list(by_id.values()), {"files": receipts, "records": len(by_id)}


def _minute(ts: float) -> int:
    return int(ts // 60) * 60


def _window(record: Mapping[str, Any]) -> tuple[float, float] | None:
    ref = record.get("market_context") or {}
    start, end = _finite(ref.get("window_start_ts")), _finite(ref.get("window_end_ts"))
    if start is None or end is None or end < start or end - start > MAX_HOLD_WINDOW_SEC:
        return None
    return start, end


def needed_minutes(records: Iterable[Mapping[str, Any]]) -> set[int]:
    need: set[int] = set()
    for rec in records:
        window = _window(rec)
        if window:
            need.update(range(_minute(window[0]) - 60, _minute(window[1]) + 60, 60))
    return need


def load_market_context(mirror: Path | None, minutes: set[int]) -> dict[int, dict[str, Any]]:
    """Only the minute rows a hold window needs; only the joined fields are retained."""
    out: dict[int, dict[str, Any]] = {}
    if not mirror or not mirror.is_dir() or not minutes:
        return out
    for path in sorted(p for p in mirror.iterdir() if _MC_ROTATION.match(p.name)):
        for row in _read_jsonl(path):
            ts = _finite(row.get("minute_ts"))
            if ts is None or int(ts) not in minutes:
                continue
            out[int(ts)] = {"minute_ts": int(ts), "derivatives": row.get("derivatives"),
                            "liquidations": row.get("liquidations"), "regime": row.get("regime")}
    return out


def enrich(record: dict[str, Any], mc: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """Market-context join and missing entry-context fill; identical for runtime and backfill rows."""
    ref = record.get("market_context") or {}
    window = _window(record)
    if window is None or ref.get("join") != "DEFERRED_ANALYZER_JOIN":
        return record
    start, end = window
    rows = [mc[m] for m in range(_minute(start) - 60, _minute(end) + 60, 60) if m in mc]
    record["market_context"] = {**ref, **sxp.hold_market_context(rows, start, end)}
    entry_regime = record["market_context"].get("entry_regime") or {}
    ctx = record.get("entry_context")
    if isinstance(ctx, dict) and ctx.get("rv_pct_rank") is None and entry_regime.get("rank_pct") is not None:
        ctx["rv_pct_rank"] = entry_regime.get("rank_pct")
        ctx["rv_label"] = entry_regime.get("label")
        ctx["rv_source"] = "MARKET_CONTEXT_1M_TRAILING_REGIME"
        ctx["missing"] = sorted(k for k in ctx.get("missing") or () if k not in ("rv_pct_rank", "rv_label"))
    return record


def wilson(wins: int, n: int, z: float = 1.96) -> list[float | None]:
    if n <= 0:
        return [None, None]
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def cluster_bootstrap_ci(values: list[float], ts: list[float]) -> dict[str, Any]:
    """95% CI of the mean over 1 h clusters (same seed/method family as the genome)."""
    n = len(values)
    if n < 2:
        return {"ci_bp": [None, None], "clusters": n}
    import numpy as np  # noqa: PLC0415

    v, t = np.asarray(values, dtype=np.float64), np.asarray(ts, dtype=np.float64)
    _, inv = np.unique(np.floor(t / CLUSTER_SEC).astype(np.int64), return_inverse=True)
    sums, counts = np.bincount(inv, weights=v), np.bincount(inv).astype(np.float64)
    c = len(sums)
    draw = np.random.default_rng(BOOTSTRAP_SEED).integers(0, c, size=(BOOTSTRAP_RESAMPLES, c))
    means = sums[draw].sum(axis=1) / counts[draw].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"ci_bp": [round(float(lo), 2), round(float(hi), 2)], "clusters": int(c)}


def exit_stats(rows: list[tuple[float, Mapping[str, Any]]], ref_by_trade: Mapping[str, Mapping[str, float]],
               exit_id: str) -> dict[str, Any]:
    vals = [(ts, row) for ts, row in rows if _finite(row.get("net_bp")) is not None]
    n = len(vals)
    if not n:
        return {"n": 0, "unavailable": len(rows)}
    nets = [float(row["net_bp"]) for _ts, row in vals]
    wins = sum(1 for x in nets if x > 0)
    givebacks = sum(1 for _ts, row in vals if sxp.is_giveback(row))
    meaningful = sum(1 for _ts, row in vals
                     if (_finite(row.get("mfe_before_exit_bp")) or 0.0) >= sxp.MEANINGFUL_PROFIT_BP)
    exit_min = [float(row["exit_t_sec"]) / 60.0 for _ts, row in vals if _finite(row.get("exit_t_sec")) is not None]
    out = {
        "n": n, "unavailable": len(rows) - n, "ev_bp": round(sum(nets) / n, 2),
        **cluster_bootstrap_ci(nets, [ts for ts, _r in vals]),
        "win_rate": round(wins / n, 4), "win_rate_ci": wilson(wins, n),
        "giveback_rate": round(givebacks / n, 4), "givebacks": givebacks,
        "meaningful_profit_trades": meaningful,
        "giveback_rate_of_meaningful": round(givebacks / meaningful, 4) if meaningful else None,
        "triggered_rate": round(sum(1 for _ts, row in vals if row.get("triggered")) / n, 4),
        "mean_exit_min": round(sum(exit_min) / len(exit_min), 2) if exit_min else None,
        "total_bp": round(sum(nets), 2),
    }
    for ref_id in REFERENCE_EXITS:
        if ref_id == exit_id:
            continue
        deltas = [float(row["net_bp"]) - ref_by_trade[row["_trade"]][ref_id] for _ts, row in vals
                  if ref_id in ref_by_trade.get(row["_trade"], {})]
        if deltas:
            out[f"delta_vs_{ref_id}_bp"] = round(sum(deltas) / len(deltas), 2)
            out[f"delta_vs_{ref_id}_n"] = len(deltas)
    return out


def _group_rows(recs: list[Mapping[str, Any]]) -> dict[str, Any]:
    per_exit: dict[str, list] = defaultdict(list)
    kinds: dict[str, str] = {}
    signal_exit: dict[str, list] = defaultdict(list)
    ref_by_trade: dict[str, dict[str, float]] = defaultdict(dict)
    filled = [r for r in recs if r.get("filled")]
    for rec in filled:
        ts = _finite(rec.get("fill_ts")) or 0.0
        for row in rec.get("shadow_exits") or ():
            per_exit[row.get("id")].append((ts, {**row, "_trade": rec.get("record_id")}))
            kinds[row.get("id")] = row.get("kind")
            if row.get("id") in REFERENCE_EXITS and _finite(row.get("net_bp")) is not None:
                ref_by_trade[rec.get("record_id")][row["id"]] = float(row["net_bp"])
    signal_refs: dict[str, dict[str, float]] = defaultdict(dict)
    for rec in recs:
        if rec.get("filled"):
            continue
        ts = _finite(rec.get("signal_ts")) or 0.0
        for row in (rec.get("counterfactual") or {}).get("taker_shadow_exits") or ():
            signal_exit[row.get("id")].append((ts, {**row, "_trade": rec.get("record_id")}))
            kinds.setdefault(row.get("id"), row.get("kind"))
            if row.get("id") in REFERENCE_EXITS and _finite(row.get("net_bp")) is not None:
                signal_refs[rec.get("record_id")][row["id"]] = float(row["net_bp"])
    horizons = {}
    for minutes in sxp.HORIZONS_MIN:
        cells = [((r.get("horizons") or {}).get(str(minutes)) or {}) for r in filled]
        mfe = [c["mfe_bp"] for c in cells if c.get("observed") and _finite(c.get("mfe_bp")) is not None]
        mae = [c["mae_bp"] for c in cells if c.get("observed") and _finite(c.get("mae_bp")) is not None]
        horizons[str(minutes)] = {"n": len(mfe), "mean_mfe_bp": round(sum(mfe) / len(mfe), 2) if mfe else None,
                                  "mean_mae_bp": round(sum(mae) / len(mae), 2) if mae else None}
    return {
        "records": len(recs), "filled": len(filled), "unfilled_signals": len(recs) - len(filled),
        "shadow_exit_set_ids": sorted({str(r.get("shadow_exit_set_id")) for r in recs}),
        "exits": [{"id": sid, "kind": kinds.get(sid), **exit_stats(rows, ref_by_trade, sid)}
                  for sid, rows in per_exit.items()],
        "signal_taker_exits": [{"id": sid, "kind": kinds.get(sid), **exit_stats(rows, signal_refs, sid)}
                               for sid, rows in signal_exit.items()],
        "horizons": horizons,
    }


def aggregate(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    cohorts: dict[str, dict[str, Any]] = {}
    for cohort in (COHORT_LIVE, COHORT_BACKFILL):
        subset = [r for r in records if r.get("_cohort") == cohort]
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for rec in subset:
            groups[sxp.group_key(rec)].append(rec)
        cohorts[cohort] = {
            "cohort": cohort, "role": COHORT_ROLES[cohort], "records": len(subset),
            "groups": [{"group": group, **_group_rows(recs)} for group, recs in sorted(groups.items())],
        }
    return cohorts


def coverage(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = defaultdict(int)
    missing: dict[str, int] = defaultdict(int)
    joined = 0
    for rec in records:
        by_source[str(rec.get("source"))] += 1
        for key in (rec.get("entry_context") or {}).get("missing") or ():
            missing[key] += 1
        joined += 1 if (rec.get("market_context") or {}).get("join") == "JOINED" else 0
    return {"by_source": dict(by_source), "entry_context_missing": dict(missing),
            "market_context_joined": joined, "records": len(records)}


def shadow_exit_sets() -> dict[str, Any]:
    try:
        import combo_pathway_config as registry  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        return {"error": type(exc).__name__}
    sets: dict[str, Any] = {}
    for lane in (None, *registry.ACTIVE_TILE_ORDER):
        spec = registry.tile_shadow_exit_set(lane)
        cell = sets.setdefault(sxp.shadow_exit_set_id(spec), {"lanes": [], "spec": [dict(s) for s in spec]})
        cell["lanes"].append(lane or "DEFAULT")
    return sets


def build_report(mirror: Path | None, backfill_dir: Path | None) -> dict[str, Any]:
    started = time.time()
    records, receipts = load_records(record_sources(mirror, backfill_dir))
    mc = load_market_context(mirror, needed_minutes(records))
    for rec in records:
        enrich(rec, mc)
    try:
        from research.fill_model import fill_model_declaration  # noqa: PLC0415
        declaration = fill_model_declaration(exit_latency_sec=sxp.EXIT_LATENCY_SEC)
    except Exception as exc:  # noqa: BLE001
        declaration = {"fill_model": "REALISTIC_V1", "declaration_error": type(exc).__name__}
    return {
        "schema": SCHEMA, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "record_schema": sxp.SCHEMA, "fill_model": declaration, "meaningful_profit_bp": sxp.MEANINGFUL_PROFIT_BP,
        "headline_cohort": COHORT_LIVE, "cohort_roles": COHORT_ROLES,
        "sources": {"mirror": str(mirror) if mirror else None,
                    "backfill_dir": str(backfill_dir) if backfill_dir else None, **receipts},
        "shadow_exit_sets": shadow_exit_sets(), "coverage": coverage(records), "cohorts": aggregate(records),
        "market_context_minutes_loaded": len(mc), "build_sec": round(time.time() - started, 2),
        "observation_only": True,
    }


def write_report(report: Mapping[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / REPORT_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the shadow-exit side-by-side report (read-only inputs).")
    parser.add_argument("--mirror", default=os.getenv("SHADOW_EXIT_MIRROR", DEFAULT_MIRROR))
    parser.add_argument("--out-dir", default=os.getenv("SHADOW_EXIT_OUT_DIR", DEFAULT_OUT_DIR))
    parser.add_argument("--backfill-dir", default=None)
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    backfill = Path(args.backfill_dir) if args.backfill_dir else out_dir / "backfill"
    report = build_report(Path(args.mirror), backfill)
    path = write_report(report, out_dir)
    print(json.dumps({"report": str(path), "records": report["sources"]["records"],
                      "build_sec": report["build_sec"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
