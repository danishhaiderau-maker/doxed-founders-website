#!/usr/bin/env python3
"""
ChatGPT-safe research bundle — atomic ZIP write + manifest with trade counts.

Output: research/downloads/chatgpt_research_bundle.zip
        research/downloads/BUNDLE_MANIFEST.json
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent
RESEARCH_ROOT = AGENT_ROOT / "research"
OUT_DIR = RESEARCH_ROOT / "downloads"
ZIP_NAME = "chatgpt_research_bundle.zip"
MANIFEST_NAME = "BUNDLE_MANIFEST.json"

KEY_CSV = (
    "trades_3factor.csv",
    "decisions_3factor.csv",
    "blocked_signals_3factor.csv",
)
KEY_JSON_ROOT = (
    "benchmark_vs_lanes_report.json",
    "lane_retirement_report.json",
    "lane_definition_report.json",
    "research_compact_summary.json",
    "report_manifest.json",
    "pathway_scorecard.json",
    "lane_pnl_ledger.json",
    "real_edge_summary.json",
    "regime_leaderboard.json",
    "regime_confidence_matrix.json",
    "top_conditional_edges.json",
    "regime_stability_report.json",
    "roster_policy.json",
)
KEY_TXT = ("executive_summary.txt", "research_findings.txt", "research_highlights.txt")

_SEARCH_ROOTS = (
    AGENT_ROOT,
    RESEARCH_ROOT,
)


def _find_file(name: str) -> Path | None:
    for root in _SEARCH_ROOTS:
        for candidate in (root / name, root / "reports" / name):
            if candidate.is_file():
                return candidate
    return None


def _trade_stats(path: Path) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8", errors="replace")))
    by_lane = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    weekend = weekday = 0
    for r in rows:
        lane = (r.get("research_lane") or "UNKNOWN").upper()
        try:
            pnl = float(r.get("net_pnl_usd") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        by_lane[lane]["n"] += 1
        by_lane[lane]["pnl"] = round(by_lane[lane]["pnl"] + pnl, 2)
        ts = r.get("close_ts") or r.get("ts") or ""
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.weekday() >= 5:
                weekend += 1
            else:
                weekday += 1
        except Exception:
            pass
    return {
        "total_trades": len(rows),
        "weekend_trades": weekend,
        "weekday_trades": weekday,
        "by_lane": dict(by_lane),
    }


def _session_archive_count() -> int:
    for arch in (RESEARCH_ROOT / "research_session_archives", AGENT_ROOT / "research_session_archives"):
        if arch.is_dir():
            return sum(1 for d in arch.iterdir() if d.is_dir() and d.name.startswith("session_"))
    return 0


def _atomic_zip_build(files: list[tuple[Path, str]], out_zip: Path) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(out_zip.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for src, arc in files:
                zf.write(src, arcname=arc)
        with zipfile.ZipFile(tmp_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise zipfile.BadZipFile(f"corrupt member: {bad}")
        shutil.move(str(tmp_path), str(out_zip))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def build(agent_root: Path | None = None) -> tuple[Path, dict]:
    global AGENT_ROOT, RESEARCH_ROOT, OUT_DIR
    if agent_root is not None:
        AGENT_ROOT = Path(agent_root).resolve()
        RESEARCH_ROOT = AGENT_ROOT / "research" if (AGENT_ROOT / "research").is_dir() else AGENT_ROOT
        OUT_DIR = RESEARCH_ROOT / "downloads"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).isoformat()
    to_pack: list[tuple[Path, str]] = []
    manifest: dict = {
        "schema": "chatgpt_research_bundle_manifest_v1",
        "generated_at": stamp,
        "purpose": "Upload this ZIP to ChatGPT for regime/lane analysis",
        "files_included": [],
        "trade_stats": None,
        "session_archives_on_disk": _session_archive_count(),
    }

    trades = _find_file("trades_3factor.csv")
    if trades:
        manifest["trade_stats"] = _trade_stats(trades)
        to_pack.append((trades, f"csv/{trades.name}"))
        manifest["files_included"].append(f"csv/{trades.name}")

    for acc_path in (
        RESEARCH_ROOT / "research_accumulator" / "trades_accumulated.csv",
        AGENT_ROOT / "research_accumulator" / "trades_accumulated.csv",
    ):
        if acc_path.is_file():
            to_pack.append((acc_path, "accumulator/trades_accumulated.csv"))
            manifest["files_included"].append("accumulator/trades_accumulated.csv")
            manifest["accumulator_trades"] = max(0, sum(1 for _ in acc_path.open(encoding="utf-8")) - 1)
            break

    for name in KEY_CSV:
        if name == "trades_3factor.csv":
            continue
        p = _find_file(name)
        if p:
            to_pack.append((p, f"csv/{p.name}"))
            manifest["files_included"].append(f"csv/{p.name}")

    for name in KEY_JSON_ROOT:
        p = _find_file(name)
        if p:
            to_pack.append((p, f"reports/{p.name}"))
            manifest["files_included"].append(f"reports/{p.name}")

    for name in KEY_TXT:
        p = _find_file(name)
        if p:
            to_pack.append((p, f"summaries/{p.name}"))
            manifest["files_included"].append(f"summaries/{p.name}")

    for src_name, arc in (
        ("trading_sessions_complete_v2.zip", "archives/trading_sessions_complete_v2.zip"),
    ):
        p = OUT_DIR / src_name
        if p.is_file() and p.stat().st_size > 1_000_000:
            try:
                with zipfile.ZipFile(p) as z:
                    if z.testzip() is None and "continuous_session_timeline.json" in z.namelist():
                        data = z.read("continuous_session_timeline.json")
                        tl_path = OUT_DIR / "_temp_timeline.json"
                        tl_path.write_bytes(data)
                        to_pack.append((tl_path, "continuous_session_timeline.json"))
                        manifest["files_included"].append("continuous_session_timeline.json")
                        manifest["timeline_sessions"] = len(json.loads(data).get("sessions") or [])
            except Exception:
                pass

    if not to_pack:
        raise FileNotFoundError(
            "No research files found — run analyzer once from agent root (trades_3factor.csv missing)."
        )

    readme = f"""ChatGPT Research Bundle
Generated: {stamp}
Total trades in CSV: {manifest['trade_stats']['total_trades'] if manifest.get('trade_stats') else 'n/a'}
Session archives on disk: {manifest['session_archives_on_disk']}

Upload this entire ZIP to ChatGPT. Start with BUNDLE_MANIFEST.json and csv/trades_3factor.csv.
"""
    readme_path = OUT_DIR / "_temp_readme.txt"
    readme_path.write_text(readme, encoding="utf-8")
    to_pack.append((readme_path, "README.txt"))

    manifest_path = OUT_DIR / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    to_pack.append((manifest_path, MANIFEST_NAME))

    out_zip = OUT_DIR / ZIP_NAME
    _atomic_zip_build(to_pack, out_zip)

    for t in (OUT_DIR / "_temp_readme.txt", OUT_DIR / "_temp_timeline.json"):
        t.unlink(missing_ok=True)

    manifest["zip_path"] = str(out_zip.resolve())
    manifest["zip_size_mb"] = round(out_zip.stat().st_size / (1024 * 1024), 2)
    manifest["zip_verified"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_zip, manifest


if __name__ == "__main__":
    path, meta = build()
    print(path)
    print(json.dumps(meta, indent=2))
