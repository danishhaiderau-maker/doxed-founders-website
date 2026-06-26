#!/usr/bin/env python3
"""
Build one ZIP with every trading-session archive + live reports + CSVs +
CONTINUOUS timeline (incl. weekend vs weekday from trades).

Usage:
  python build_complete_session_bundle.py
  python build_complete_session_bundle.py --out downloads/my_bundle.zip

Default output: downloads/trading_sessions_complete.zip (under Final Bots root)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

FINAL_BOTS_ROOT = Path(__file__).resolve().parent
DEFAULT_AGENT_ROOT = FINAL_BOTS_ROOT
ARCHIVE_DIR = "research_session_archives"
REPORTS_DIR = "reports"
DOWNLOADS_DIR = "downloads"
BENCHMARK_LANE = "CONTINUOUS"

CSV_CANDIDATES = (
    "trades_3factor.csv",
    "decisions_3factor.csv",
    "blocked_3factor.csv",
)


def _read_json(path: Path, default=None):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _continuous_from_benchmark(path: Path) -> dict | None:
    data = _read_json(path)
    if not data:
        return None
    lanes = data.get("lanes") or {}
    cont = lanes.get(BENCHMARK_LANE) or {}
    if not cont:
        return None
    return {
        "fills": int(cont.get("real_fills") or cont.get("fills") or 0),
        "approves": int(cont.get("approves") or 0),
        "pnl_usd": float(cont.get("net_pnl_real") or cont.get("net_pnl_usd") or 0),
        "ev_per_approve": float(cont.get("per_approve_ev") or 0),
    }


def _session_timeline(history_root: Path) -> list:
    arch = history_root / ARCHIVE_DIR
    if not arch.is_dir():
        return []
    rows = []
    for folder in sorted(arch.iterdir()):
        if not folder.is_dir() or not folder.name.startswith("session_"):
            continue
        meta = _read_json(folder / "session_meta.json") or {}
        bench_path = folder / REPORTS_DIR / "benchmark_vs_lanes_report.json"
        if not bench_path.is_file():
            alt = list(folder.glob("**/benchmark_vs_lanes_report.json"))
            bench_path = alt[0] if alt else bench_path
        cont = _continuous_from_benchmark(bench_path) or {}
        gen = meta.get("generated_at") or ""
        dow = weekday = is_weekend = None
        try:
            dt = datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
            dow = dt.strftime("%A")
            weekday = dt.weekday()
            is_weekend = weekday >= 5
        except Exception:
            pass
        rows.append({
            "session_id": meta.get("session_id") or folder.name,
            "generated_at": gen,
            "day_of_week": dow,
            "is_weekend": is_weekend,
            "analyzer_sync_id": meta.get("analyzer_sync_id"),
            "data_scope": meta.get("data_scope"),
            "session_trades": meta.get("trades"),
            "session_net_pnl_usd": meta.get("net_pnl_usd"),
            "session_wr_pct": meta.get("win_rate_pct"),
            "continuous_fills": cont.get("fills"),
            "continuous_approves": cont.get("approves"),
            "continuous_pnl_usd": cont.get("pnl_usd"),
            "continuous_ev_per_approve": cont.get("ev_per_approve"),
        })
    return rows


def _trades_weekend_breakdown(trades_path: Path) -> dict:
    if not trades_path.is_file():
        return {"note": "trades_3factor.csv not found", "rows": []}
    by_dow = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl_usd": 0.0, "continuous_trades": 0, "continuous_pnl_usd": 0.0})
    continuous_rows = []
    ts_cols = ("close_ts", "ts", "entry_ts", "open_ts")
    with trades_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                pnl = float(row.get("net_pnl_usd") or 0)
            except (TypeError, ValueError):
                pnl = 0.0
            lane = str(row.get("research_lane") or "").upper()
            raw_ts = None
            for c in ts_cols:
                if row.get(c):
                    raw_ts = row[c]
                    break
            dow = "UNKNOWN"
            is_weekend = None
            if raw_ts:
                try:
                    dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                    dow = dt.strftime("%A")
                    is_weekend = dt.weekday() >= 5
                except Exception:
                    pass
            bucket = "weekend" if is_weekend else ("weekday" if is_weekend is False else "unknown")
            by_dow[dow]["trades"] += 1
            by_dow[dow]["pnl_usd"] = round(by_dow[dow]["pnl_usd"] + pnl, 2)
            if pnl > 0:
                by_dow[dow]["wins"] += 1
            if lane == BENCHMARK_LANE:
                by_dow[dow]["continuous_trades"] += 1
                by_dow[dow]["continuous_pnl_usd"] = round(by_dow[dow]["continuous_pnl_usd"] + pnl, 2)
                continuous_rows.append({
                    "trade_id": row.get("trade_id"),
                    "ts": raw_ts,
                    "day_of_week": dow,
                    "is_weekend": is_weekend,
                    "net_pnl_usd": round(pnl, 2),
                    "exit_reason": row.get("exit_reason"),
                    "ai_win_prob": row.get("ai_win_prob"),
                    "dir": row.get("dir") or row.get("final_direction"),
                })
    weekend = {"trades": 0, "wins": 0, "pnl_usd": 0.0, "continuous_trades": 0, "continuous_pnl_usd": 0.0}
    weekday = {"trades": 0, "wins": 0, "pnl_usd": 0.0, "continuous_trades": 0, "continuous_pnl_usd": 0.0}
    for dow, stats in by_dow.items():
        target = weekend if dow in ("Saturday", "Sunday") else weekday if dow != "UNKNOWN" else None
        if target is None:
            continue
        for k in target:
            target[k] += stats[k]
    for agg in (weekend, weekday):
        t = agg["trades"]
        agg["wr_pct"] = round(100.0 * agg["wins"] / t, 1) if t else 0.0
        agg["pnl_usd"] = round(agg["pnl_usd"], 2)
        agg["continuous_pnl_usd"] = round(agg["continuous_pnl_usd"], 2)
    return {
        "source": str(trades_path),
        "by_day_of_week": dict(by_dow),
        "weekend_vs_weekday": {"weekend": weekend, "weekday": weekday},
        "continuous_trades": continuous_rows,
    }


SESSION_INCLUDE_NAMES = frozenset({
    "session_meta.json",
    "executive_summary.txt",
    "research_findings.txt",
    "research_highlights.txt",
    "research_coverage.txt",
    "research_compact_summary.json",
    "report_manifest.json",
})


def _add_session_folder(zf: zipfile.ZipFile, folder: Path, arc_prefix: str, seen: set) -> int:
    """One archive folder — skip nested reports/history duplicates."""
    if not folder.is_dir():
        return 0
    n = 0
    for name in SESSION_INCLUDE_NAMES:
        p = folder / name
        if p.is_file():
            rel = f"{arc_prefix}/{name}"
            if rel not in seen:
                zf.write(p, arcname=rel)
                seen.add(rel)
                n += 1
    rep = folder / REPORTS_DIR
    if rep.is_dir():
        for p in rep.glob("*.json"):
            rel = f"{arc_prefix}/{REPORTS_DIR}/{p.name}"
            if rel not in seen:
                zf.write(p, arcname=rel)
                seen.add(rel)
                n += 1
    return n


def _add_tree(zf: zipfile.ZipFile, folder: Path, arc_prefix: str, seen: set):
    if not folder.is_dir():
        return 0
    n = 0
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if "/history/" in path.as_posix():
            continue
        rel = f"{arc_prefix}/{path.relative_to(folder).as_posix()}"
        if rel in seen:
            continue
        seen.add(rel)
        zf.write(path, arcname=rel)
        n += 1
    return n


def build_bundle(
    history_root: Path,
    live_root: Path | None = None,
    out_path: Path | None = None,
) -> Path:
    history_root = Path(history_root)
    live_root = Path(live_root or DEFAULT_AGENT_ROOT)
    out_path = Path(out_path or (history_root / DOWNLOADS_DIR / "trading_sessions_complete.zip"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".zip.tmp")

    timeline = _session_timeline(history_root)
    trades_path = live_root / "trades_3factor.csv"
    if not trades_path.is_file():
        trades_path = history_root / "trades_3factor.csv"
    weekend = _trades_weekend_breakdown(trades_path)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    readme = f"""Trading Sessions — Complete Bundle
Generated: {stamp}
History root: {history_root}
Live root: {live_root}

Contents:
  session_index.json          — flat list of all archived analyzer runs
  continuous_session_timeline.json — CONTINUOUS lane stats per archive snapshot
  continuous_weekend_breakdown.json — weekday vs weekend from trades CSV
  continuous_trades.csv       — every CONTINUOUS fill with day-of-week
  sessions/                   — full report tree per archived session ({len(timeline)} folders)
  live/                       — latest reports + summaries from live bot folder
  csv/                        — raw pipeline CSVs (trades, decisions, blocked)

Open continuous_session_timeline.json to see how CONTINUOUS PnL evolved across sessions.
Open continuous_weekend_breakdown.json for weekend vs weekday split.
"""

    seen = set()
    file_count = 0
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("README.txt", readme)
        zf.writestr("session_index.json", json.dumps({"generated_at": stamp, "sessions": timeline}, indent=2))
        zf.writestr("continuous_session_timeline.json", json.dumps({"generated_at": stamp, "sessions": timeline}, indent=2))
        zf.writestr("continuous_weekend_breakdown.json", json.dumps({"generated_at": stamp, **weekend}, indent=2))
        cont_csv = weekend.get("continuous_trades") or []
        if cont_csv:
            import io
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=list(cont_csv[0].keys()))
            w.writeheader()
            w.writerows(cont_csv)
            zf.writestr("continuous_trades.csv", buf.getvalue())

        arch = history_root / ARCHIVE_DIR
        if arch.is_dir():
            for folder in sorted(arch.iterdir()):
                if folder.is_dir() and folder.name.startswith("session_"):
                    file_count += _add_session_folder(zf, folder, f"sessions/{folder.name}", seen)

        live_names = (
            "executive_summary.txt", "research_findings.txt", "research_highlights.txt",
            "research_coverage.txt", "research_compact_summary.json", "report_manifest.json",
            "analysis_dashboard.html", "analyzer_run.log", "benchmark_vs_lanes_report.json",
        )
        for name in live_names:
            p = live_root / name
            if p.is_file():
                arc = f"live/{name}"
                if arc not in seen:
                    zf.write(p, arcname=arc)
                    seen.add(arc)
                    file_count += 1
        file_count += _add_tree(zf, live_root / REPORTS_DIR, "live/reports", seen)
        file_count += _add_tree(zf, live_root / REPORTS_DIR / "all_data", "live/reports/all_data", seen)

        for name in CSV_CANDIDATES:
            for root in (live_root, history_root):
                p = root / name
                if p.is_file():
                    arc = f"csv/{name}"
                    if arc not in seen:
                        zf.write(p, arcname=arc)
                        seen.add(arc)
                        file_count += 1
                    break

    with zipfile.ZipFile(tmp_path, "r") as zf:
        bad = zf.testzip()
        if bad:
            raise zipfile.BadZipFile(f"corrupt member: {bad}")
    promoted = out_path
    try:
        if out_path.exists():
            out_path.unlink()
        tmp_path.rename(out_path)
    except OSError:
        # Windows: target may be open in browser/ChatGPT — write verified sibling instead
        promoted = out_path.with_name(out_path.stem + "_v2.zip")
        if promoted.exists():
            promoted.unlink()
        shutil.copy2(tmp_path, promoted)
        tmp_path.unlink(missing_ok=True)

    print(f"Wrote {promoted} ({file_count} files, {len(timeline)} session archives)")
    return promoted


def main():
    ap = argparse.ArgumentParser(description="Build complete trading-session ZIP bundle")
    ap.add_argument("--history-root", default=str(FINAL_BOTS_ROOT))
    ap.add_argument("--live-root", default=str(DEFAULT_AGENT_ROOT))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out = Path(args.out) if args.out else None
    path = build_bundle(Path(args.history_root), Path(args.live_root), out)
    print(path.resolve())


if __name__ == "__main__":
    main()
