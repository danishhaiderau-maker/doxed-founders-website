#!/usr/bin/env python3
"""
v103 end-of-session insight runner — run after 48h collection or anytime.

Produces:
  execution_funnel_summary.json
  fill_quality_report.json
  approval_ev_report.json
  v103_session_insights.json (combined)
  Plus refreshes research KPI + profitable reject reports.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SESSION_INSIGHTS_FILE = "v103_session_insights.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_subprocess(cmd: list, cwd: str) -> dict:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
        return {"ok": r.returncode == 0, "stdout": (r.stdout or "")[-2000:], "stderr": (r.stderr or "")[-1000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main() -> int:
    cwd = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    os.chdir(cwd)
    print(f"=== v103 Execution Insight Report ===")
    print(f"Working directory: {cwd}")
    print(f"Time: {_utc_iso()}\n")

    from execution_funnel import refresh_all_execution_reports
    funnel_data = refresh_all_execution_reports(cwd)
    fs = funnel_data.get("funnel_summary") or {}
    print("--- Execution Funnel ---")
    print(f"  APPROVE: {fs.get('approve_count')}")
    print(f"  ORDER:   {fs.get('order_submitted_count')} ({fs.get('approval_to_order_rate_pct')}%)")
    print(f"  FILL:    {fs.get('filled_count')} ({fs.get('approve_to_fill_rate_pct')}%)")
    print(f"  CLOSE:   {fs.get('closed_count')}")
    print(f"  Terminal: {fs.get('terminal_reasons')}")

    fq = funnel_data.get("fill_quality") or {}
    print("\n--- Fill Quality ---")
    print(f"  Buckets: {fq.get('buckets')}")
    print(f"  avg_distance_from_market: {fq.get('avg_distance_from_market_pct')}%")
    print(f"  avg_missed_by: {fq.get('avg_missed_by_pct')}%")

    ev = funnel_data.get("approval_ev") or {}
    print("\n--- Stage EV ---")
    for stage in ("AI_APPROVE_EV", "ORDER_PLACED_EV", "FILLED_EV", "TRADED_EV"):
        block = ev.get(stage) or {}
        print(f"  {stage}: n={block.get('n')} sum=${block.get('sum_pnl_usd')} avg=${block.get('avg_pnl_usd')}")

    research_kpi = {}
    try:
        from research_kpi_engine import refresh_all_research_kpis
        research_kpi = refresh_all_research_kpis(cwd)
        fr = research_kpi.get("false_reject") or {}
        print("\n--- Research KPI ---")
        print(f"  false_reject_rate: {fr.get('false_reject_rate_pct')}%")
        print(f"  missed_profit: ${fr.get('missed_profit_usd')}")
    except Exception as e:
        research_kpi = {"error": str(e)}
        print(f"\n--- Research KPI skipped: {e}")

    analyzer_run = run_subprocess(
        [sys.executable, "analyzer_research_engine_v62.py", "--once"],
        cwd,
    )
    print("\n--- Analyzer ---")
    print(f"  ok={analyzer_run.get('ok')}")

    payload = {
        "generated_at": _utc_iso(),
        "cwd": cwd,
        "version": "v1.0.3-execution-insights",
        "execution_funnel": funnel_data,
        "research_kpi": research_kpi,
        "analyzer_run": analyzer_run,
    }
    out_path = os.path.join(cwd, SESSION_INSIGHTS_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote combined report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
