"""Indicator Edge export for Grok Strategist: scoreboard + coverage audit + per-bar labels in one JSON file.

Written by the laptop ``indicator_edge_cycle`` next to the scoreboard (``indicator_edge_export.json``) and
published by ``scripts/run-segment-analyzer-cycle.ps1`` as a ``--supplemental`` member of the analyzer mirror
bundle, so it lands on Fly under ``analyzer_generations/<generation>/reports/`` and Strategist's
``mirror_incremental.sh`` pulls it with the rest of the generation. Fly only accepts .json/.html/.txt/.log
members (no .jsonl), hence one JSON document with the labels in a compact, exactly invertible columnar form::

    {"schema": "indicator_edge_export_v1", "generated_at": ..., "scoreboard": {...}, "coverage": {...},
     "labels": {"schema": "indicator_edge_labels_v1", "columns": [...], "dicts": {col: [values]},
                "side_features": [...], "rows": [[...], ...], "rows_total": N, "truncated": bool}}

``expand(doc)`` (or ``python research/indicator_edge_export.py --expand export.json --out labels.jsonl``)
rebuilds the exact ``indicator_edge_labels.jsonl`` rows. Observation only: nothing here reads or writes Fly,
tiles, orders or the relay.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import indicator_edge_spec as spec  # noqa: E402
from research import indicator_edge_labels as labels  # noqa: E402
from research import indicator_forward_scorer as sc  # noqa: E402

SCHEMA = "indicator_edge_export_v1"
EXPORT_FILE = "indicator_edge_export.json"
# 30 days of 3-minute bars (~350 B per row -> ~5 MB), far under Fly's 50 MB member cap.
MAX_LABEL_ROWS = 30 * 480
SIDE_CODE = {1: "+", -1: "-", 0: "0"}
SIDE_DECODE = {"+": 1, "-": -1, "0": 0}
DICT_COLUMNS = ("data_epoch_id", "feature_set_sha") + tuple("regime." + k for k in labels.REGIME_KEYS)
FWD_SIMPLE = ("exec_long_bp", "exec_short_bp", "mfe_bp", "mae_bp")
FEATURE_KEYS = ("feature", "indicator", "num", "family", "role", "variant", "label", "best_window_min",
                "best_regime", "signals_per_day", "cluster_rep", "independent", "best")
LIST_CAP = 25


def _windows() -> list[str]:
    return [str(w) for w in sc.WINDOWS]


def columns() -> list[str]:
    ws = _windows()
    cols = ["bar_ts", "bar_close_ts", "decision_ts", "data_epoch_id", "feature_set_sha", "health_ok", "late",
            "prereg_eligible"]
    cols += ["regime." + k for k in labels.REGIME_KEYS]
    cols += [f"fwd.mid_bp.{lat}.{w}" for lat in sc.LATENCIES for w in ws]
    cols += [f"fwd.{name}.{w}" for name in FWD_SIMPLE for w in ws]
    cols.append("sides")
    return cols


def compact_labels(rows: Sequence[Mapping[str, Any]], max_rows: int = MAX_LABEL_ROWS) -> dict[str, Any]:
    """Columnar form of ``indicator_edge_labels_v1`` rows (newest ``max_rows`` kept)."""
    total = len(rows)
    rows = list(rows)[-max_rows:] if max_rows and total > max_rows else list(rows)
    cols, ws = columns(), _windows()
    side_features = spec.scored_feature_ids()
    dicts: dict[str, list] = {c: [] for c in DICT_COLUMNS}
    index: dict[str, dict] = {c: {} for c in DICT_COLUMNS}
    out = []
    for r in rows:
        flat: dict[str, Any] = {k: r.get(k) for k in cols[:8]}
        for k in labels.REGIME_KEYS:
            flat["regime." + k] = (r.get("regime") or {}).get(k)
        fwd = r.get("fwd") or {}
        for lat in sc.LATENCIES:
            for w in ws:
                flat[f"fwd.mid_bp.{lat}.{w}"] = ((fwd.get("mid_bp") or {}).get(str(lat)) or {}).get(w)
        for name in FWD_SIMPLE:
            for w in ws:
                flat[f"fwd.{name}.{w}"] = (fwd.get(name) or {}).get(w)
        sides = r.get("sides") or {}
        flat["sides"] = "".join(SIDE_CODE.get(sides.get(f), ".") if f in sides else "." for f in side_features)
        for c in DICT_COLUMNS:
            v = flat[c]
            if v not in index[c]:
                index[c][v] = len(dicts[c])
                dicts[c].append(v)
            flat[c] = index[c][v]
        out.append([flat[c] for c in cols])
    return {"schema": labels.SCHEMA, "file_equivalent": labels.LABEL_FILE, "columns": cols, "dicts": dicts,
            "side_features": side_features, "side_codes": {"+": 1, "-": -1, "0": 0, ".": "absent"},
            "rows": out, "rows_total": total, "rows_exported": len(out), "truncated": len(out) < total,
            "matured": sum(1 for r in rows if r.get("matured")),
            "first_bar_utc": sc._utc(rows[0]["bar_close_ts"]) if rows else None,
            "last_bar_utc": sc._utc(rows[-1]["bar_close_ts"]) if rows else None}


def expand(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Exact ``indicator_edge_labels_v1`` rows from an export document (or its ``labels`` block)."""
    lab = doc.get("labels", doc)
    cols, dicts, feats = lab["columns"], lab["dicts"], lab["side_features"]
    ws = [c.rsplit(".", 1)[1] for c in cols if c.startswith(f"fwd.mid_bp.{sc.PRIMARY_LAT}.")]
    lats = sorted({int(c.split(".")[2]) for c in cols if c.startswith("fwd.mid_bp.")}, key=list(sc.LATENCIES).index)
    out = []
    for vals in lab["rows"]:
        flat = dict(zip(cols, vals))
        for c in DICT_COLUMNS:
            flat[c] = dicts[c][flat[c]]
        mid = {str(lat): {w: flat[f"fwd.mid_bp.{lat}.{w}"] for w in ws} for lat in lats}
        fwd: dict[str, Any] = {"mid_bp": mid}
        for name in FWD_SIMPLE:
            fwd[name] = {w: flat[f"fwd.{name}.{w}"] for w in ws}
        prim = mid[str(sc.PRIMARY_LAT)]
        out.append({
            "schema": lab["schema"], "bar_ts": flat["bar_ts"], "bar_close_ts": flat["bar_close_ts"],
            "decision_ts": flat["decision_ts"], "data_epoch_id": flat["data_epoch_id"],
            "feature_set_sha": flat["feature_set_sha"], "health_ok": flat["health_ok"], "late": flat["late"],
            "prereg_eligible": flat["prereg_eligible"],
            "regime": {k: flat["regime." + k] for k in labels.REGIME_KEYS},
            "sides": {f: SIDE_DECODE[ch] for f, ch in zip(feats, flat["sides"]) if ch != "."},
            "fwd": fwd,
            "dir": {w: (None if v is None else (v > 0) - (v < 0)) for w, v in prim.items()},
            "matured": all(v is not None for v in prim.values()),
        })
    return out


def _small(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _small(v) for k, v in value.items() if not (isinstance(v, list) and len(v) > LIST_CAP)}
    return value


def scoreboard(report: Mapping[str, Any]) -> dict[str, Any]:
    """The scoreboard without per-window/daily/bootstrap detail (that stays in indicator_edge_report.json)."""
    out = {k: v for k, v in report.items() if k not in ("features", "clusters", "combinations")}
    out["clusters"] = {"independent_count": (report.get("clusters") or {}).get("independent_count"),
                       "representatives": (report.get("clusters") or {}).get("representatives")}
    out["combinations"] = _small(report.get("combinations") or {})
    out["features"] = [{k: f.get(k) for k in FEATURE_KEYS} for f in report.get("features") or []]
    return out


def build(report: Mapping[str, Any], label_rows: Sequence[Mapping[str, Any]], coverage: Mapping[str, Any] | None,
          *, now: float | None = None, max_rows: int = MAX_LABEL_ROWS) -> dict[str, Any]:
    now = float(now if now is not None else time.time())
    return {"schema": SCHEMA, "generated_at": sc._utc(now), "generated_at_ts": now,
            "source": "laptop research/indicator_edge_cycle.py (analyzer mirror supplemental)",
            "observation_only": True,
            "readme": "scoreboard = frozen-prereg Indicator Edge scoreboard (detail in indicator_edge_report.json); "
                      "coverage = per-indicator liveness audit (WARMUP reason INSUFFICIENT_SWINGS is not DEAD); "
                      "labels = per-bar forward outcomes, expand with research/indicator_edge_export.py --expand.",
            "scoreboard": scoreboard(report), "coverage": coverage,
            "labels": compact_labels(label_rows, max_rows=max_rows)}


def write(doc: Mapping[str, Any], out_dir: str | os.PathLike) -> str:
    path = Path(out_dir) / EXPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, separators=(",", ":"), allow_nan=False, default=str)
    os.replace(tmp, path)
    return str(path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Expand an indicator_edge_export.json into indicator_edge_labels.jsonl")
    ap.add_argument("--expand", required=True, help="path to indicator_edge_export.json")
    ap.add_argument("--out", required=True, help="output .jsonl path")
    args = ap.parse_args(argv)
    doc = json.loads(Path(args.expand).read_text(encoding="utf-8"))
    rows = expand(doc)
    labels.write_jsonl(rows, args.out)
    print(json.dumps({"rows": len(rows), "out": args.out, "truncated": doc["labels"].get("truncated")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
