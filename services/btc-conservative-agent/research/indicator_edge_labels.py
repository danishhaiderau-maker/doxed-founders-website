"""Per-bar forward-outcome labels and a per-indicator coverage audit for the Indicator Edge stream.

Offline and read-only (laptop analyzer cycle or Grok Strategist's box mirror; never imported by the Fly
runtime). ``indicator_bars_v1`` rows carry the feature vector only; the forward outcome is joined later from the
Bitfinex 1 s tape. The scorer keeps those outcomes in memory and publishes aggregates, so nothing downstream had
a per-bar table to study a single indicator or a combination on. This module writes one:

``indicator_edge_labels.jsonl`` - one row per bar (``SCHEMA``)::

    bar_ts, bar_close_ts, decision_ts, data_epoch_id, feature_set_sha, health_ok, late, prereg_eligible,
    regime   {vol_tercile, trend_state, session, spread_bucket, ttf_bucket, ai_class, ai_side},
    sides    {feature_id: -1 | 0 | +1}           # scored features whose status is AVAILABLE
    fwd      {mid_bp: {"2": {"3": bp, "15": .., "60": .., "120": ..}, "9": {...}},
              exec_long_bp, exec_short_bp, mfe_bp, mae_bp: {"3": .., ...}}   # primary 2 s latency
    dir      {"3": +1 | -1 | 0 | null, ...}       # sign of the 2 s-latency mid return
    matured  true when every window has an observed outcome

Outcomes come from :func:`indicator_forward_scorer.outcomes` (same decision time, latencies, windows and hole
censor as the scoreboard), so a feature's hit is ``sides[f] * dir[w] > 0`` and its net move is
``sides[f] * fwd.mid_bp["2"][w] - round_trip_cost_bp``. Unmatured windows are ``null``.

``coverage(rows)`` is the per-indicator audit behind ``INDICATOR-COVERAGE`` reports: for each of the 52
Appendix A indicators, presence, % non-null raw, distinct values, min/median/max, signal counts and a status
(OK / MISSING / DEAD / CONSTANT / WARMUP / UNAVAILABLE) plus the stamped ``reason`` for WARMUP / UNAVAILABLE
(e.g. ``INSUFFICIENT_SWINGS`` for a quiet-regime PITCHFORK, which is not DEAD).

Observation only: no tile, order, relay or Fly state is read or written.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import indicator_edge_spec as spec  # noqa: E402
from research import indicator_edge_prereg as prereg  # noqa: E402
from research import indicator_forward_scorer as sc  # noqa: E402
from strategy_lab import tape as tape_mod  # noqa: E402

SCHEMA = "indicator_edge_labels_v1"
COVERAGE_SCHEMA = "indicator_edge_coverage_v1"
LABEL_FILE = "indicator_edge_labels.jsonl"
COVERAGE_FILE = "indicator_edge_coverage.json"
REGIME_KEYS = ("vol_tercile", "trend_state", "session", "spread_bucket", "ttf_bucket", "ai_class", "ai_side")
# A raw value that is null on more than this share of AVAILABLE rows is DEAD (the indicator claims to be computed).
DEAD_NULL_SHARE = 0.95
# Rows written before engine ``indicator_engine_v1_20261004b`` stamped a quiet-regime PITCHFORK as AVAILABLE with
# raw null and score 0; that return was only reachable with < 3 confirmed swings, so such a cell is read as
# WARMING_UP / INSUFFICIENT_SWINGS (what the current engine writes, with ``status_reasons``).
LEGACY_NULL_AVAILABLE_REASON = {"PITCHFORK_12H@F:STATE": "INSUFFICIENT_SWINGS"}


def _cell(row: Mapping[str, Any], fid: str) -> tuple[list | None, str | None]:
    """``(cell, reason)`` with legacy AVAILABLE-but-null cells normalised to WARMING_UP plus their reason."""
    cell = (row.get("f") or {}).get(fid)
    if not (isinstance(cell, list) and len(cell) >= 4):
        return None, None
    reason = (row.get("status_reasons") or {}).get(fid)
    legacy = LEGACY_NULL_AVAILABLE_REASON.get(fid)
    if legacy and cell[3] == spec.STATUS_AVAILABLE and cell[0] is None and not cell[1]:
        return [None, None, None, spec.STATUS_WARMING_UP], legacy
    return cell, reason


def _bp(x: float) -> float | None:
    return sc._r(x, 3) if math.isfinite(x) else None


def _sides(row: Mapping[str, Any], fids: Sequence[str]) -> dict[str, int]:
    f = row.get("f") or {}
    out = {}
    for fid in fids:
        cell = f.get(fid)
        if isinstance(cell, list) and len(cell) >= 4 and cell[3] == spec.STATUS_AVAILABLE \
                and isinstance(cell[1], (int, float)):
            out[fid] = int(np.sign(cell[1]))
    return out


def label_rows(rows: Sequence[Mapping[str, Any]], oc: Mapping[str, Any], dts: np.ndarray,
               eligible: Sequence[bool | None] | None = None) -> list[dict[str, Any]]:
    """Label ``rows`` with the outcome arrays ``oc`` computed at decision times ``dts`` (same order)."""
    fids = spec.scored_feature_ids()
    windows = [str(w) for w in sc.WINDOWS]
    out = []
    for i, row in enumerate(rows):
        mid = {str(lat): {w: _bp(oc["mid"][lat][i, j]) for j, w in enumerate(windows)} for lat in sc.LATENCIES}
        prim = mid[str(sc.PRIMARY_LAT)]
        fwd = {"mid_bp": mid}
        for key, name in (("exec_long", "exec_long_bp"), ("exec_short", "exec_short_bp"),
                          ("up", "mfe_bp"), ("dn", "mae_bp")):
            fwd[name] = {w: _bp(oc[key][i, j]) for j, w in enumerate(windows)}
        regime = row.get("regime") or {}
        out.append({
            "schema": SCHEMA,
            "bar_ts": int(row["bar_ts"]),
            "bar_close_ts": int(row["bar_close_ts"]),
            "decision_ts": round(float(dts[i]), 3),
            "data_epoch_id": row.get("data_epoch_id"),
            "feature_set_sha": row.get("feature_set_sha"),
            "health_ok": bool((row.get("health") or {}).get("ok")),
            "late": bool(row.get("late")),
            "prereg_eligible": None if eligible is None else eligible[i],
            "regime": {k: regime.get(k) for k in REGIME_KEYS},
            "sides": _sides(row, fids),
            "fwd": fwd,
            "dir": {w: (None if v is None else int(np.sign(v))) for w, v in prim.items()},
            "matured": all(v is not None for v in prim.values()),
        })
    return out


def write_jsonl(rows: Sequence[Mapping[str, Any]], path: str | os.PathLike) -> str:
    """Atomic full rewrite (the labels are a pure function of the bar rows and the tape)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
    os.replace(tmp, path)
    return str(path)


def write_from_context(ctx: Mapping[str, Any], out_dir: str) -> dict[str, Any]:
    """Cycle hook: label the scorer's eligible rows from the outcomes it already computed (no second tape load)."""
    rows, oc, dts = ctx.get("rows"), ctx.get("outcomes"), ctx.get("dts")
    if not rows or oc is None or dts is None:
        return {"labels": None, "label_rows": 0}
    labelled = label_rows(rows, oc, dts, eligible=[True] * len(rows))
    path = write_jsonl(labelled, Path(out_dir) / LABEL_FILE)
    return {"labels": path, "label_rows": len(labelled),
            "label_rows_matured": sum(1 for r in labelled if r["matured"])}


def build(data_dir: str, *, prereg_root: Path | None = None, tape: Any = "load",
          rows: Sequence[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Label every bar row in ``data_dir`` (Strategist mirror or laptop tree), eligible or not.

    ``prereg_eligible`` is filled when a pre-registration chain is given, else ``null``.
    """
    rows = list(rows) if rows is not None else sc.load_bar_rows(data_dir)
    if not rows:
        return []
    eligible = None
    if prereg_root is not None:
        chain_rows, chain = prereg.load_chain(Path(prereg_root) / prereg.FROZEN_FILE)
        freeze = prereg.feature_set_freeze(chain_rows) if chain["chain_ok"] else None
        elig, _ = sc.eligible_rows(rows, freeze)
        keep = {id(r) for r in elig}
        eligible = [id(r) in keep for r in rows]
    dts = np.array([sc.decision_ts(r) for r in rows])
    if isinstance(tape, str):
        span = max(sc.WINDOWS) * 60 + max(sc.LATENCIES) + 120
        hist = tape_mod.default_history_sources() if tape_mod.laptop_defaults_enabled() else None
        tape = tape_mod.load_bitfinex_tape(data_dir, float(dts.min()) - 60, float(dts.max()) + span, history=hist)
    return label_rows(rows, sc.outcomes(tape, dts), dts, eligible=eligible)


# ---------------------------------------------------------------- coverage audit

def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-indicator presence and liveness over ``rows`` (all 52 Appendix A indicators, registry order).

    Raw statistics use the indicator's first stored feature (fast horizon, first variant); ``signals`` counts
    non-zero direction scores over every scored variant. Status precedence: MISSING (never stored),
    UNAVAILABLE / WARMUP (no AVAILABLE row; majority status), DEAD (AVAILABLE but raw null on > 95% of those
    rows), CONSTANT (one distinct raw value over >= 2 rows), else OK. ``signals == 0`` on a scored indicator is
    reported as ``silent`` (no direction call in this window) but is not a defect by itself.
    """
    n = len(rows)
    out = []
    counts: dict[str, int] = {}
    all_fids = spec.feature_ids()
    for ind in spec.INDICATORS:
        fids = [f for f in all_fids if spec.indicator_of(f)["id"] == ind["id"]]
        primary = fids[0]
        present = sum(1 for r in rows if any(f in (r.get("f") or {}) for f in fids))
        pairs = [_cell(r, primary) for r in rows]
        cells = [c for c, _ in pairs if c is not None]
        reasons: dict[str, int] = {}
        for c, why in pairs:
            if why and c is not None and c[3] != spec.STATUS_AVAILABLE:
                reasons[why] = reasons.get(why, 0) + 1
        st = {}
        for c in cells:
            st[c[3]] = st.get(c[3], 0) + 1
        avail = [c for c in cells if c[3] == spec.STATUS_AVAILABLE]
        raws = [_num(c[0]) for c in cells]
        nn = [x for x in raws if x is not None]
        avail_nn = [x for x in (_num(c[0]) for c in avail) if x is not None]
        signals = 0
        if ind["scored"]:
            for r in rows:
                for fid in fids:
                    c, _ = _cell(r, fid)
                    if c is not None and c[3] == spec.STATUS_AVAILABLE and _num(c[1]):
                        signals += 1
        if not present:
            status = "MISSING"
        elif not avail:
            status = "UNAVAILABLE" if st.get(spec.STATUS_UNAVAILABLE, 0) >= st.get(spec.STATUS_WARMING_UP, 0) \
                else "WARMUP"
        elif len(avail) - len(avail_nn) > DEAD_NULL_SHARE * len(avail):
            status = "DEAD"
        elif len(avail_nn) >= 2 and len(set(avail_nn)) == 1:
            status = "CONSTANT"
        else:
            status = "OK"
        counts[status] = counts.get(status, 0) + 1
        out.append({
            "num": ind["num"], "id": ind["id"], "family": ind["family"], "scored": ind["scored"],
            "primary_feature": primary, "features": len(fids), "present_rows": present,
            "pct_non_null": None if not n else round(100.0 * len(nn) / n, 1),
            "distinct": len(set(nn)),
            "min": sc._r(min(nn), 6) if nn else None,
            "median": sc._r(statistics.median(nn), 6) if nn else None,
            "max": sc._r(max(nn), 6) if nn else None,
            "status_counts": {spec.STATUS_NAMES.get(k, k): v for k, v in sorted(st.items())},
            "signals": signals if ind["scored"] else None,
            "silent": bool(ind["scored"] and status == "OK" and signals == 0),
            "status": status,
            # dominant reason a non-AVAILABLE cell gave (e.g. INSUFFICIENT_SWINGS), null when none was stamped
            "reason": max(reasons, key=reasons.get) if reasons and status in ("WARMUP", "UNAVAILABLE") else None,
            "reason_counts": dict(sorted(reasons.items())),
        })
    epochs = sorted({str(r.get("data_epoch_id")) for r in rows})
    return {"schema": COVERAGE_SCHEMA, "rows": n, "data_epoch_ids": epochs,
            "first_bar_utc": sc._utc(rows[0]["bar_close_ts"]) if rows else None,
            "last_bar_utc": sc._utc(rows[-1]["bar_close_ts"]) if rows else None,
            "counts": counts, "indicators": out}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Indicator Edge per-bar forward labels + coverage (offline, read-only)")
    ap.add_argument("--data-dir", default=sc.DEFAULT_DATA, help="folder holding indicator_bars_v1.jsonl and the 1 s tape")
    ap.add_argument("--out-dir", default=sc.DEFAULT_OUT)
    ap.add_argument("--prereg-root", default=None, help="forward-tracker folder; fills prereg_eligible when given")
    ap.add_argument("--since-ts", type=float, default=None, help="only bars closing at or after this unix time")
    ap.add_argument("--epoch", default=None, help="only rows stamped with this data_epoch_id")
    ap.add_argument("--coverage-only", action="store_true", help="skip the tape join; write the coverage audit only")
    args = ap.parse_args(argv)
    for p in (args.data_dir, args.out_dir, args.prereg_root):
        if p and "\\onedrive\\" in str(p).lower():
            raise SystemExit(f"refusing a OneDrive path: {p}")
    rows = sc.load_bar_rows(args.data_dir)
    if args.since_ts is not None:
        rows = [r for r in rows if float(r.get("bar_close_ts") or 0) >= args.since_ts]
    if args.epoch:
        rows = [r for r in rows if r.get("data_epoch_id") == args.epoch]
    cov = coverage(rows)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    cov_path = Path(args.out_dir) / COVERAGE_FILE
    tmp = cov_path.with_name(cov_path.name + ".tmp")
    tmp.write_text(json.dumps(cov, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, cov_path)
    res: dict[str, Any] = {"rows": len(rows), "coverage": str(cov_path), "coverage_counts": cov["counts"]}
    if not args.coverage_only:
        labelled = build(args.data_dir, prereg_root=Path(args.prereg_root) if args.prereg_root else None, rows=rows)
        res["labels"] = write_jsonl(labelled, Path(args.out_dir) / LABEL_FILE) if labelled else None
        res["label_rows"] = len(labelled)
        res["label_rows_matured"] = sum(1 for r in labelled if r["matured"])
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
