#!/usr/bin/env python3
"""Analyzer vNext Phase 1 — integrity checks, chase fix, dashboard display."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "services" / "btc-conservative-agent"
ANALYZER = AGENT / "research" / "analyzer_research_engine_v62.py"
DASHBOARD = AGENT / "research" / "research_dashboard.py"
ROOT_ANALYZER = AGENT / "analyzer_research_engine_v62.py"


def patch_analyzer(text: str) -> str:
    if "ANALYZER_INTEGRITY_REPORT_FILE" not in text:
        text = text.replace(
            'EXIT_LADDER_SIMULATOR_REPORT_FILE = "exit_ladder_simulator_report.json"\n',
            'EXIT_LADDER_SIMULATOR_REPORT_FILE = "exit_ladder_simulator_report.json"\n'
            'ANALYZER_INTEGRITY_REPORT_FILE = "analyzer_integrity_report.json"\n',
            1,
        )

    if "def _agent_data_path(" not in text:
        text = text.replace(
            "def robust_read_csv(filepath, name=\"file\"):",
            '''def _agent_data_path(filename: str) -> str:
    """Resolve CSV/JSONL under agent root when cwd is research/."""
    if os.path.isfile(filename):
        return filename
    parent = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    alt = os.path.normpath(os.path.join(parent, filename))
    if os.path.isfile(alt):
        return alt
    env_root = os.getenv("BTC_AGENT_DATA_DIR")
    if env_root:
        env_alt = os.path.join(env_root, filename)
        if os.path.isfile(env_alt):
            return env_alt
    return filename


def robust_read_csv(filepath, name="file"):''',
            1,
        )
        text = text.replace(
            '    if not os.path.exists(filepath):\n        print(f"⚠️ {name} not found',
            '    filepath = _agent_data_path(filepath)\n    if not os.path.exists(filepath):\n        print(f"⚠️ {name} not found',
            1,
        )

    if "def _normalize_lane_label(" not in text:
        insert_before = "def chase_attribution_report(trades=None, session=None):"
        helper = '''
def _normalize_lane_label(lane) -> str:
    """Fix pandas NaN / empty funnel lane values."""
    if lane is None:
        return "UNKNOWN"
    s = str(lane).strip().upper()
    if not s or s in {"NAN", "NONE", "NULL", "UNKNOWN", "<NA>"}:
        return "UNKNOWN"
    return s


def _resolve_chase_count(tid: str, funnel_count, trade_chase: dict) -> tuple[int, str]:
    """Prefer trades_3factor.limit_chase_count when funnel under-reports chases."""
    csv_count = trade_chase.get(tid)
    try:
        funnel_n = int(funnel_count or 0)
    except (TypeError, ValueError):
        funnel_n = 0
    if csv_count is not None:
        try:
            csv_n = int(csv_count or 0)
        except (TypeError, ValueError):
            csv_n = 0
        if csv_n > funnel_n:
            return csv_n, "trades_3factor.limit_chase_count"
        return max(funnel_n, csv_n), (
            "execution_funnel.limit_chase_count" if funnel_n else "trades_3factor.limit_chase_count"
        )
    return funnel_n, "execution_funnel.limit_chase_count"


'''
        text = text.replace(insert_before, helper + insert_before, 1)

    # Chase attribution: trade meta maps + CSV precedence + lane fix
    old_chase_block = '''        if "limit_chase_count" in work.columns:
            for _, t in work.iterrows():
                tid = str(t.get("trade_id") or "")
                if tid:
                    trade_chase[tid] = int(pd.to_numeric(t.get("limit_chase_count"), errors="coerce") or 0)'''
    new_chase_block = '''        trade_lane = {}
        trade_hold = {}
        if "limit_chase_count" in work.columns:
            for _, t in work.iterrows():
                tid = str(t.get("trade_id") or "")
                if tid:
                    trade_chase[tid] = int(pd.to_numeric(t.get("limit_chase_count"), errors="coerce") or 0)
                    if "research_lane" in work.columns:
                        trade_lane[tid] = _normalize_lane_label(t.get("research_lane"))
                    hold = t.get("dur_min") if "dur_min" in work.columns else t.get("duration_min")
                    if hold is not None and pd.notna(hold):
                        trade_hold[tid] = round(float(hold), 2)'''
    if old_chase_block in text:
        text = text.replace(old_chase_block, new_chase_block, 1)
    else:
        text = text.replace(
            "    trade_chase = {}\n    if trades is not None",
            "    trade_chase = {}\n    trade_lane = {}\n    trade_hold = {}\n    if trades is not None",
            1,
        )

    old_cc = '''        chase_count = 0
        if fill_row and fill_row.get("limit_chase_count") is not None:
            chase_count = int(fill_row.get("limit_chase_count") or 0)
        elif chase_rows:
            chase_count = int(chase_rows[-1].get("limit_chase_count") or len(chase_rows))
        elif expire_row and expire_row.get("limit_chase_count") is not None:
            chase_count = int(expire_row.get("limit_chase_count") or 0)
        if (not chase_count) and tid in trade_chase:
            chase_count = int(trade_chase[tid] or 0)

        lane = (
            (order_row or {}).get("research_lane")
            or (fill_row or {}).get("research_lane")
            or (chase_rows[0].get("research_lane") if chase_rows else None)
            or (expire_row or {}).get("research_lane")
            or "UNKNOWN"
        )'''
    new_cc = '''        funnel_cc = 0
        if fill_row and fill_row.get("limit_chase_count") is not None:
            funnel_cc = int(fill_row.get("limit_chase_count") or 0)
        elif chase_rows:
            funnel_cc = int(chase_rows[-1].get("limit_chase_count") or len(chase_rows))
        elif expire_row and expire_row.get("limit_chase_count") is not None:
            funnel_cc = int(expire_row.get("limit_chase_count") or 0)
        chase_count, chase_count_source = _resolve_chase_count(tid, funnel_cc, trade_chase)

        lane = _normalize_lane_label(
            (order_row or {}).get("research_lane")
            or (fill_row or {}).get("research_lane")
            or (chase_rows[0].get("research_lane") if chase_rows else None)
            or (expire_row or {}).get("research_lane")
            or trade_lane.get(tid)
            or "UNKNOWN"
        )'''
    if old_cc in text:
        text = text.replace(old_cc, new_cc, 1)

    if '"chase_count": chase_count,' in text and '"chase_count_source"' not in text.split("attributions.append(attr)", 1)[0][-800:]:
        text = text.replace(
            '            "chase_count": chase_count,\n            "chase_events_logged": len(chase_rows),',
            '            "chase_count": chase_count,\n            "chase_count_source": chase_count_source,\n            "avg_hold_min": trade_hold.get(tid),\n            "chase_events_logged": len(chase_rows),',
            1,
        )

    # Bucket stats with avg_hold
    old_bucket = '''def _chase_bucket_stats(attributions):
    order = ["0", "1", "2", "3", "4", "5+"]
    buckets = {k: {"trades": 0, "wins": 0, "sum_pnl_usd": 0.0, "win_rate_pct": 0.0, "ev_usd": 0.0} for k in order}
    for row in attributions or []:
        if not row.get("net_pnl_usd") and row.get("win") is None:
            continue
        key = _chase_count_bucket(row.get("chase_count"))
        b = buckets[key]
        b["trades"] += 1
        pnl = float(row.get("net_pnl_usd") or 0)
        b["sum_pnl_usd"] = round(b["sum_pnl_usd"] + pnl, 2)
        if row.get("win") or pnl > 0:
            b["wins"] += 1
    for key, b in buckets.items():
        n = b["trades"]
        if n:
            b["win_rate_pct"] = round(100.0 * b["wins"] / n, 1)
            b["ev_usd"] = round(b["sum_pnl_usd"] / n, 2)
        b["sum_pnl_usd"] = round(b["sum_pnl_usd"], 2)
    return buckets'''
    new_bucket = '''def _chase_bucket_stats(attributions):
    order = ["0", "1", "2", "3", "4", "5+"]
    buckets = {
        k: {
            "trades": 0, "wins": 0, "sum_pnl_usd": 0.0, "win_rate_pct": 0.0, "ev_usd": 0.0,
            "avg_hold_min": 0.0, "_hold_n": 0,
        }
        for k in order
    }
    for row in attributions or []:
        if row.get("net_pnl_usd") is None and row.get("win") is None:
            continue
        key = _chase_count_bucket(row.get("chase_count"))
        b = buckets[key]
        b["trades"] += 1
        pnl = float(row.get("net_pnl_usd") or 0)
        b["sum_pnl_usd"] = round(b["sum_pnl_usd"] + pnl, 2)
        if row.get("win") or pnl > 0:
            b["wins"] += 1
        hold = row.get("avg_hold_min")
        if hold is not None:
            try:
                b["avg_hold_min"] = round(b["avg_hold_min"] + float(hold), 2)
                b["_hold_n"] += 1
            except (TypeError, ValueError):
                pass
    for key, b in buckets.items():
        n = b["trades"]
        hold_n = b.pop("_hold_n", 0)
        if hold_n:
            b["avg_hold_min"] = round(b["avg_hold_min"] / hold_n, 2)
        else:
            b["avg_hold_min"] = None
        if n:
            b["win_rate_pct"] = round(100.0 * b["wins"] / n, 1)
            b["ev_usd"] = round(b["sum_pnl_usd"] / n, 2)
        b["sum_pnl_usd"] = round(b["sum_pnl_usd"], 2)
    return buckets'''
    if old_bucket in text:
        text = text.replace(old_bucket, new_bucket, 1)

    # AI_SCAN coordinator stats in benchmark
    if '"ai_scan_coordinator"' not in text:
        text = text.replace(
            '''        elif lane == "AI_SCAN":
            v2_lane_extra = {
                "coordinator_note": "Coordinator lane — approvals only, 0 fills by design.",
            }''',
            '''        elif lane == "AI_SCAN":
            coord = _ai_scan_coordinator_stats(decisions, ai_log)
            v2_lane_extra = {
                "coordinator_note": "Coordinator — 0 fills by design",
                "ai_scan_coordinator": coord,
                "coordinator_rejects": coord.get("rejects", 0),
                "coordinator_skipped": coord.get("skipped", 0),
                "coordinator_timeouts": coord.get("timeouts", 0),
            }''',
            1,
        )

    if "def _ai_scan_coordinator_stats(" not in text:
        text = text.replace(
            "def chase_attribution_report(trades=None, session=None):",
            '''def _ai_scan_coordinator_stats(decisions=None, ai_log=None) -> dict:
    """AI_SCAN funnel — approvals/rejects/skipped/timeout from decisions + ai log."""
    out = {"approvals": 0, "rejects": 0, "skipped": 0, "timeouts": 0, "total": 0}
    if decisions is not None and not decisions.empty:
        work = decisions.copy()
        if "ai_decision_text" in work.columns:
            txt = work["ai_decision_text"].fillna("").astype(str).str.upper()
            out["approvals"] = int((txt == "APPROVE").sum())
            out["rejects"] = int((txt == "REJECT").sum())
            out["timeouts"] = int(txt.str.contains("ERROR|TIMEOUT", regex=True).sum())
        if "skip_stage" in work.columns:
            out["skipped"] = int(work["skip_stage"].fillna("").astype(str).str.upper().eq("COOLDOWN").sum())
        elif "reason" in work.columns:
            out["skipped"] = int(work["reason"].fillna("").astype(str).str.contains("AI_COOLDOWN", regex=False).sum())
        out["total"] = int(len(work))
    funnel = out["approvals"] + out["rejects"] + out["skipped"] + out["timeouts"]
    out["funnel_sum"] = funnel
    return out


def chase_attribution_report(trades=None, session=None):''',
            1,
        )
        # benchmark_vs_lanes_report needs decisions param passed - patch signature if needed
        text = text.replace(
            "def benchmark_vs_lanes_report(trades, session=None, blocked=None, shadow_report=None, all_trades=None):",
            "def benchmark_vs_lanes_report(trades, session=None, blocked=None, shadow_report=None, all_trades=None, decisions=None, ai_log=None):",
            1,
        )

    # Exit leak recommendations with finding/gain template
    old_recs = '''def _exit_leak_recommendations(reasons: list) -> list:
    """Actionable items from exit-reason leakage ranking."""
    recs = []
    for row in reasons or []:
        reason = str(row.get("exit_reason") or "")
        template = EXIT_LEAK_ACTION_MAP.get(reason)
        if not template:
            continue
        recs.append({
            "exit_reason": reason,
            "trades": row.get("trades"),
            "left_on_table_usd": row.get("left_on_table_usd"),
            "priority": template["priority"],
            "action": template["action"],
            "script_hint": template["script_hint"],
        })'''
    new_recs = '''def _exit_leak_recommendations(reasons: list) -> list:
    """Finding → Recommendation → Expected gain per exit leak source."""
    recs = []
    for row in reasons or []:
        reason = str(row.get("exit_reason") or "")
        template = EXIT_LEAK_ACTION_MAP.get(reason)
        if not template:
            continue
        left = float(row.get("left_on_table_usd") or 0)
        n = int(row.get("trades") or 0)
        avg_left = float(row.get("avg_left_usd") or 0)
        capture = float(row.get("capture_ratio_pct") or 0)
        finding = (
            f"{reason} on {n} trades left ${left:.0f} on table "
            f"(avg ${avg_left:.2f}/trade, {capture:.0f}% capture)."
        )
        expected_gain = (
            f"Recover ~10–25% of leaked value (${left * 0.1:.0f}–${left * 0.25:.0f}) "
            f"if {reason} exits tighten by one ladder rung or delayed trigger."
        )
        recs.append({
            "exit_reason": reason,
            "trades": n,
            "left_on_table_usd": left,
            "priority": template["priority"],
            "finding": finding,
            "recommendation": template["action"],
            "expected_gain": expected_gain,
            "action": template["action"],
            "script_hint": template["script_hint"],
        })'''
    if old_recs in text:
        text = text.replace(old_recs, new_recs, 1)

    # Ladder sim unrealistic flag
    if '"unrealistic_vs_actual"' not in text:
        text = text.replace(
            '''        profiles_out.append({
            "profile_id": pid,
            "label": prof["label"],
            "ladder": prof["ladder"],
            "trades_simulated": n,
            "sum_pnl_usd": sum_pnl,
            "avg_pnl_usd": round(sum_pnl / n, 2) if n else 0.0,
            "wr_pct": round(100.0 * cell["wins"] / n, 1) if n else 0.0,
            "ladder_exit_pct": round(100.0 * cell["ladder_exits"] / n, 1) if n else 0.0,
            "thesis_cut_pct": round(100.0 * cell["thesis_cuts"] / n, 1) if n else 0.0,
            "delta_vs_actual_usd": round(sum_pnl - actual_sum, 2) if actual_n and n else None,
        })''',
            '''        delta = round(sum_pnl - actual_sum, 2) if actual_n and n else None
        unrealistic = bool(actual_sum > 0 and sum_pnl > actual_sum * 2)
        profiles_out.append({
            "profile_id": pid,
            "label": prof["label"],
            "ladder": prof["ladder"],
            "trades_simulated": n,
            "sum_pnl_usd": sum_pnl,
            "avg_pnl_usd": round(sum_pnl / n, 2) if n else 0.0,
            "wr_pct": round(100.0 * cell["wins"] / n, 1) if n else 0.0,
            "ladder_exit_pct": round(100.0 * cell["ladder_exits"] / n, 1) if n else 0.0,
            "thesis_cut_pct": round(100.0 * cell["thesis_cuts"] / n, 1) if n else 0.0,
            "delta_vs_actual_usd": delta,
            "unrealistic_vs_actual": unrealistic,
        })''',
            1,
        )
        text = text.replace(
            '        "Counterfactual tick replay on executed trades only. Alternate ladder profiles assume "',
            '        "HINDSIGHT COUNTERFACTUAL: tick replay on executed trade paths only (not perfect live fills). "',
            1,
        )
        text = text.replace(
            '''    if best and best.get("trades_simulated"):
        print(
            f"  Best profile: {best['profile_id']} sum=${best['sum_pnl_usd']:.2f} "
            f"(actual=${actual_sum:.2f}, Δ=${best.get('delta_vs_actual_usd')}) {PIPELINE_ENFORCEMENT_TAG}"
        )''',
            '''    if best and best.get("trades_simulated"):
        flag = " UNREALISTIC (>2× actual)" if best.get("unrealistic_vs_actual") else ""
        print(
            f"  Best profile: {best['profile_id']} sum=${best['sum_pnl_usd']:.2f} "
            f"(actual=${actual_sum:.2f}, Δ=${best.get('delta_vs_actual_usd')}){flag} {PIPELINE_ENFORCEMENT_TAG}"
        )''',
            1,
        )

    # Type B discovery probability table
    if "def _type_b_probability_table(" not in text:
        text = text.replace(
            '    payload = {\n        "schema": "type_b_predictor_v1",',
            '''    prob_table = _type_b_probability_table(work)
    payload = {
        "schema": "type_b_predictor_v2",''',
            1,
        )
        text = text.replace(
            '        "top_separators": separators[:10],\n        "hypothesis":',
            '        "top_separators": separators[:10],\n        "probability_table": prob_table,\n        "hypothesis":',
            1,
        )
        text = text.replace(
            "def type_b_predictor_report(trades=None, session=None):",
            '''def _type_b_bucket(val, kind: str) -> str:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "unknown"
    if kind == "adx":
        if v < 20:
            return "adx<20"
        if v < 30:
            return "adx20-30"
        return "adx30+"
    if kind == "spread":
        if v <= 2:
            return "spread0-2"
        if v <= 4:
            return "spread3-4"
        return "spread5+"
    if kind == "conf":
        if v < 55:
            return "conf<55"
        if v < 65:
            return "conf55-65"
        return "conf65+"
    if kind == "vol":
        if v < 80:
            return "vol_low"
        if v < 150:
            return "vol_mid"
        return "vol_high"
    return "unknown"


def _type_b_probability_table(work: pd.DataFrame) -> list:
    """Historical P(TYPE_B | feature bucket) — discovery only, not an entry gate."""
    if work is None or work.empty:
        return []
    df = work.copy()
    df["trade_mfe_type"] = _trade_mfe_type_series(df)
    df["is_type_b"] = df["trade_mfe_type"].eq("TYPE_B")
    if "adx_at_entry" in df.columns:
        df["_adx_b"] = df["adx_at_entry"].map(lambda x: _type_b_bucket(x, "adx"))
    if "conviction_spread" in df.columns:
        df["_spread_b"] = df["conviction_spread"].map(lambda x: _type_b_bucket(x, "spread"))
    elif "directional_spread" in df.columns:
        df["_spread_b"] = df["directional_spread"].map(lambda x: _type_b_bucket(x, "spread"))
    if "ai_win_prob" in df.columns:
        df["_conf_b"] = df["ai_win_prob"].map(lambda x: _type_b_bucket(x, "conf"))
    if "volatility" in df.columns:
        df["_vol_b"] = df["volatility"].map(lambda x: _type_b_bucket(x, "vol"))
    if "context_ema_slope" in df.columns:
        df["_ema_b"] = pd.to_numeric(df["context_ema_slope"], errors="coerce").map(
            lambda x: "ema_up" if (x or 0) > 0 else ("ema_down" if (x or 0) < 0 else "ema_flat")
        )
    if "research_lane" in df.columns:
        df["_lane_b"] = df["research_lane"].fillna("").astype(str).str.upper()
    dim_cols = {
        "adx": "_adx_b", "spread": "_spread_b", "confidence": "_conf_b",
        "volatility": "_vol_b", "ema_slope": "_ema_b", "lane": "_lane_b",
    }
    rows = []
    for dim, col in dim_cols.items():
        if col not in df.columns:
            continue
        for bucket, sub in df.groupby(col, observed=True):
            if str(bucket) in ("unknown", "nan", ""):
                continue
            n = int(len(sub))
            if n < 3:
                continue
            b_n = int(sub["is_type_b"].sum())
            wr = round(100.0 * (sub["net_pnl_usd"].astype(float) > 0).mean(), 1) if "net_pnl_usd" in sub.columns else None
            rows.append({
                "dimension": dim,
                "bucket": str(bucket),
                "trades": n,
                "type_b_count": b_n,
                "type_b_probability_pct": round(100.0 * b_n / n, 1),
                "wr_pct": wr,
            })
    rows.sort(key=lambda r: (-r["type_b_probability_pct"], -r["trades"]))
    return rows[:40]


def type_b_predictor_report(trades=None, session=None):''',
            1,
        )

    # run_integrity_checks
    if "def run_integrity_checks(" not in text:
        integrity_fn = '''
def run_integrity_checks(
    trades=None,
    decisions=None,
    session=None,
    chase_payload=None,
    benchmark_report=None,
):
    """
    Validate → reconcile → display. Reports are INVALID when checks fail.
    Writes analyzer_integrity_report.json (dashboard reads before render).
    """
    checks = []
    valid = True

    def _add(name, passed, expected, found, detail=""):
        nonlocal valid
        if not passed:
            valid = False
        checks.append({
            "check": name,
            "passed": passed,
            "expected": expected,
            "found": found,
            "detail": detail,
        })

    # Trade W/L reconciliation
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in work.columns else "outcome_net_pnl_usd"
        pnl = pd.to_numeric(work[pnl_col], errors="coerce").fillna(0)
        wins = int((pnl > 0).sum())
        losses = int((pnl < 0).sum())
        breakeven = int((pnl == 0).sum())
        total = int(len(work))
        _add(
            "trades_wins_losses",
            total == wins + losses + breakeven,
            f"wins+losses+be={wins}+{losses}+{breakeven}={wins + losses + breakeven}",
            f"total_trades={total}",
        )

    # AI funnel: approvals + rejects + skipped + timeout ≈ decision rows with AI outcome
    if decisions is not None and not decisions.empty:
        d = decisions.copy()
        ai_txt = d["ai_decision_text"].fillna("").astype(str).str.upper() if "ai_decision_text" in d.columns else pd.Series([], dtype=str)
        appr = int((ai_txt == "APPROVE").sum()) if len(ai_txt) else 0
        rej = int((ai_txt == "REJECT").sum()) if len(ai_txt) else 0
        timeout = int(ai_txt.str.contains("ERROR|TIMEOUT", regex=True).sum()) if len(ai_txt) else 0
        skipped = 0
        if "skip_stage" in d.columns:
            skipped = int(d["skip_stage"].fillna("").astype(str).str.upper().eq("COOLDOWN").sum())
        elif "reason" in d.columns:
            skipped = int(d["reason"].fillna("").astype(str).str.contains("AI_COOLDOWN", regex=False).sum())
        funnel = appr + rej + skipped + timeout
        ai_rows = int((d["decision"].fillna("").astype(str).str.upper().isin(["AI", "BLOCKED"])).sum()) if "decision" in d.columns else len(d)
        _add(
            "ai_decision_funnel",
            abs(funnel - ai_rows) <= max(5, int(0.02 * len(d))),
            f"approvals+rejects+skipped+timeout={appr}+{rej}+{skipped}+{timeout}={funnel}",
            f"ai_decision_rows≈{ai_rows} (total decisions={len(d)})",
            "From decisions_3factor.ai_decision_text + skip_stage/reason",
        )

    # Chase buckets: CSV limit_chase_count vs chase_effectiveness report
    csv_buckets = {}
    if trades is not None and not trades.empty and "limit_chase_count" in trades.columns:
        work = trades.drop_duplicates(subset=["trade_id"], keep="last")
        cc = pd.to_numeric(work["limit_chase_count"], errors="coerce").fillna(0).astype(int)
        for n in cc:
            key = _chase_count_bucket(n)
            csv_buckets[key] = csv_buckets.get(key, 0) + 1
        eff = {}
        if chase_payload:
            attr = (chase_payload.get("trades") or [])
            for row in attr:
                key = _chase_count_bucket(row.get("chase_count"))
                if row.get("net_pnl_usd") is not None or row.get("win") is not None:
                    eff[key] = eff.get(key, 0) + 1
        else:
            eff_path = analyzer_report_path("chase_effectiveness_report.json")
            if os.path.isfile(eff_path):
                try:
                    with open(eff_path, encoding="utf-8") as f:
                        rep = json.load(f)
                    for k, b in (rep.get("buckets") or {}).items():
                        eff[k] = int((b or {}).get("trades") or 0)
                except Exception:
                    eff = {}
        mismatch = []
        for k in set(list(csv_buckets.keys()) + list(eff.keys())):
            if csv_buckets.get(k, 0) != eff.get(k, 0):
                mismatch.append(f"{k}: csv={csv_buckets.get(k, 0)} report={eff.get(k, 0)}")
        _add(
            "chase_count_buckets",
            not mismatch,
            str(csv_buckets),
            str(eff),
            "; ".join(mismatch[:6]) if mismatch else "trades_3factor.limit_chase_count matches report buckets",
        )

    # Lane totals vs CONTINUOUS
    if benchmark_report and trades is not None and not trades.empty and "research_lane" in trades.columns:
        lanes = (benchmark_report or {}).get("lanes") or {}
        cont = int((lanes.get("CONTINUOUS") or {}).get("real_fills") or (lanes.get("CONTINUOUS") or {}).get("fills") or 0)
        lane_sum = 0
        for ln, m in lanes.items():
            if ln in ("AI_SCAN", "EXEC_5M"):
                continue
            lane_sum += int(m.get("real_fills") or m.get("fills") or 0)
        work = trades.drop_duplicates(subset=["trade_id"])
        csv_total = int(len(work))
        _add(
            "lane_fill_reconcile",
            lane_sum >= cont and csv_total >= cont,
            f"lane_fills_sum≥CONTINUOUS({cont}), csv_trades={csv_total}",
            f"lane_fills_sum={lane_sum}",
        )

    # Genome vs completed trades (best-effort)
    genome_path = _agent_data_path(os.path.join("research", "genome", "genome_events.jsonl"))
    if not os.path.isfile(genome_path):
        genome_path = _agent_data_path("genome_events.jsonl")
    trade_ids = set()
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        trade_ids = set(trades["trade_id"].dropna().astype(str))
    genome_trade_events = 0
    if os.path.isfile(genome_path) and trade_ids:
        try:
            for row in _load_jsonl_rows(genome_path):
                if row.get("event_name") in ("TRADE_COMPLETE", "TRADE_CLOSED") and str(row.get("trade_id") or "") in trade_ids:
                    genome_trade_events += 1
        except Exception:
            pass
        if genome_trade_events:
            _add(
                "genome_vs_trades",
                genome_trade_events <= len(trade_ids) * 1.5,
                f"genome_trade_events≤{len(trade_ids) * 1.5}",
                f"genome_events={genome_trade_events}, csv_trades={len(trade_ids)}",
            )

    # signal_id linkage spot check
    if trades is not None and not trades.empty and decisions is not None and not decisions.empty:
        if "signal_id" in trades.columns and "signal_id" in decisions.columns:
            sample = trades.drop_duplicates(subset=["trade_id"]).head(20)
            linked = 0
            dec_ids = set(decisions["signal_id"].dropna().astype(str))
            for _, row in sample.iterrows():
                sid = str(row.get("signal_id") or "")
                tid = str(row.get("trade_id") or "")
                if sid and sid in dec_ids:
                    linked += 1
                elif tid and tid in set(decisions.get("trade_id", pd.Series()).dropna().astype(str)):
                    linked += 1
            _add(
                "signal_id_linkage_spot",
                linked >= min(5, len(sample) // 2),
                f"≥{min(5, len(sample) // 2)} of {len(sample)} sample trades linked",
                f"linked={linked}",
            )

    scope = _shadow_scope_label(session) if session else "SESSION"
    payload = {
        "schema": "analyzer_integrity_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_scope": scope,
        "valid": valid,
        "report_status": "VALID" if valid else "INVALID",
        "banner": None if valid else "⚠ REPORT INVALID — reconcile before trusting chase/exit tables",
        "checks": checks,
        "failed_checks": [c for c in checks if not c.get("passed")],
    }
    try:
        with open(analyzer_report_path(ANALYZER_INTEGRITY_REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        status = payload["report_status"]
        print(f"  Integrity: {status} ({len(checks)} checks, {len(payload['failed_checks'])} failed) {PIPELINE_ENFORCEMENT_TAG}")
    except Exception as exc:
        print(f"  ⚠️ Could not write {ANALYZER_INTEGRITY_REPORT_FILE}: {exc} {PIPELINE_ENFORCEMENT_TAG}")
    return payload


'''
        text = text.replace("def chase_attribution_report(trades=None, session=None):", integrity_fn + "def chase_attribution_report(trades=None, session=None):", 1)

    if "run_integrity_checks(" not in text.split("def pre_test_analytics_reports")[1][:2500]:
        text = text.replace(
            "    fast_cut_sweep_report(trades=trades, session=session)\n",
            "    fast_cut_sweep_report(trades=trades, session=session)\n"
            "    chase_payload_final = chase_payload\n"
            "    if chase_payload_final is None and os.path.isfile(CHASE_ATTRIBUTION_REPORT_FILE):\n"
            "        chase_payload_final = _load_json_report(CHASE_ATTRIBUTION_REPORT_FILE)\n"
            "    run_integrity_checks(\n"
            "        trades=trades,\n"
            "        decisions=decisions,\n"
            "        session=session,\n"
            "        chase_payload=chase_payload_final,\n"
            "        benchmark_report=benchmark_report,\n"
            "    )\n",
            1,
        )

    # Pass decisions to benchmark_vs_lanes_report calls
    for old, new in [
        (
            "benchmark_report = benchmark_vs_lanes_report(\n                trades, session=session, blocked=blocked, shadow_report=shadow_report,\n                all_trades=all_trades_unfiltered,\n            )",
            "benchmark_report = benchmark_vs_lanes_report(\n                trades, session=session, blocked=blocked, shadow_report=shadow_report,\n                all_trades=all_trades_unfiltered, decisions=decisions, ai_log=ai_log,\n            )",
        ),
        (
            "benchmark_report = benchmark_vs_lanes_report(trades=trades, session=session, shadow_report=shadow_report)",
            "benchmark_report = benchmark_vs_lanes_report(trades=trades, session=session, shadow_report=shadow_report, decisions=decisions, ai_log=ai_log)",
        ),
    ]:
        if old in text:
            text = text.replace(old, new, 1)

    return text


def patch_dashboard(text: str) -> str:
    if "ANALYZER_INTEGRITY_FILE" not in text:
        text = text.replace(
            'COMPACT_SUMMARY_FILE = "research_compact_summary.json"\n',
            'COMPACT_SUMMARY_FILE = "research_compact_summary.json"\n'
            'ANALYZER_INTEGRITY_FILE = "analyzer_integrity_report.json"\n',
            1,
        )

    if "def _integrity_payload(" not in text:
        text = text.replace(
            "def _summary_stale_meta(compact: dict) -> dict:",
            '''def _integrity_payload() -> dict:
    rep = _read_report(ANALYZER_INTEGRITY_FILE)
    if not rep:
        return {"valid": True, "report_status": "UNKNOWN", "checks": [], "banner": None}
    return rep


def _summary_stale_meta(compact: dict) -> dict:''',
            1,
        )

    if '"integrity": _integrity_payload()' not in text:
        text = text.replace(
            '        "stale": stale_meta,\n        "all_data_fallback_active": all_data_active,',
            '        "stale": stale_meta,\n        "integrity": _integrity_payload(),\n        "all_data_fallback_active": all_data_active,',
            1,
        )

    if 'id="integrity-banner"' not in text:
        text = text.replace(
            '<div id="stale-banner" class="stale-banner" style="display:none;"></div>',
            '<div id="integrity-banner" class="stale-banner" style="display:none;background:#3d2a1f;border-color:#d29922;color:#f8e3a1;"></div>\n<div id="stale-banner" class="stale-banner" style="display:none;"></div>',
            1,
        )

    # Rename Type B tab
    text = text.replace('("typeb", "Type B Research",', '("typeb", "Type B Discovery",', 1)
    text = text.replace("<h2>Type B Research</h2>", "<h2>Type B Discovery</h2>", 1)

    if 'id="typeb-prob-body"' not in text:
        text = text.replace(
            '<h3>Top separators (TYPE_B vs TYPE_A)</h3>',
            '<h3>Type B probability table (historical — not an entry gate)</h3>\n    <table><thead><tr><th>Dimension</th><th>Bucket</th><th>N</th><th>TYPE_B</th><th>P(TYPE_B)%</th><th>WR%</th></tr></thead><tbody id="typeb-prob-body"></tbody></table>\n    <h3>Top separators (TYPE_B vs TYPE_A)</h3>',
            1,
        )

    # Lane lab headers
    text = text.replace(
        "<th>Appr</th><th>Sess Fills</th><th>Chk Pass</th><th>Reject Sim</th><th>Shadow</th>",
        "<th>Appr</th><th>Paper Fills</th><th>Checker Pass</th><th>Shadow Sims</th><th>Rejects</th><th>Shadow</th>",
        1,
    )

    # Chase lane filter UI
    if 'id="chase-lane-filter"' not in text:
        text = text.replace(
            '  <section id="sec-chase">\n    <h2>Chase Analytics</h2>',
            '  <section id="sec-chase">\n    <h2>Chase Analytics</h2>\n    <label class="lane-toggle">Lane: <select id="chase-lane-filter"><option value="">Combined</option><option value="CONTINUOUS">CONTINUOUS</option><option value="AI60_SP3_VIRTUAL_CHASE">AI60 SP3</option><option value="A160_CONTEXT_CHASE_EXIT_V2">A160 V2</option></select></label>',
            1,
        )
        for sec in ("sec-chase-threshold", "sec-chase-delay", "sec-chase-iso", "sec-exit-reason-leak", "sec-ladder-sim"):
            text = text.replace(
                f'  <section id="{sec}">',
                f'  <section id="{sec}">\n    <label class="lane-toggle chase-lane-filter-wrap">Lane: <select class="chase-lane-filter"><option value="">Combined</option><option value="CONTINUOUS">CONTINUOUS</option><option value="AI60_SP3_VIRTUAL_CHASE">AI60 SP3</option><option value="A160_CONTEXT_CHASE_EXIT_V2">A160 V2</option></select></label>',
                1,
            )

    # Lane rows payload - AI_SCAN coordinator + A160 paper fills split
    if '"coordinator_rejects"' not in text.split("rows.append({")[1][:600]:
        text = text.replace(
            '"coordinator_note": m.get("coordinator_note") or "",',
            '"coordinator_note": m.get("coordinator_note") or "",\n            "coordinator_rejects": int(m.get("coordinator_rejects") or (m.get("ai_scan_coordinator") or {}).get("rejects") or 0),\n            "coordinator_skipped": int(m.get("coordinator_skipped") or (m.get("ai_scan_coordinator") or {}).get("skipped") or 0),\n            "coordinator_timeouts": int(m.get("coordinator_timeouts") or (m.get("ai_scan_coordinator") or {}).get("timeouts") or 0),',
            1,
        )

    if "def _filter_chase_attributions(" not in text:
        text = text.replace(
            "def _chase_payload():",
            '''CHASE_LANE_ALIASES = {
    "AI60": "AI60_SP3_VIRTUAL_CHASE",
    "A160": "A160_CONTEXT_CHASE_EXIT_V2",
    "A160 V2": "A160_CONTEXT_CHASE_EXIT_V2",
}


def _normalize_chase_lane(lane: str) -> str:
    u = str(lane or "").strip().upper()
    return CHASE_LANE_ALIASES.get(u, u)


def _filter_chase_attributions(rows, lane: str):
    lane = _normalize_chase_lane(lane)
    if not lane:
        return list(rows or [])
    out = []
    for row in rows or []:
        rl = _normalize_chase_lane((row or {}).get("lane") or (row or {}).get("research_lane"))
        if rl == lane:
            out.append(row)
    return out


def _chase_payload(lane: str = ""):''',
            1,
        )
        text = text.replace(
            "def _chase_payload(lane: str = \"\"):\n    attr = _read_report(\"chase_attribution_report.json\")",
            'def _chase_payload(lane: str = ""):\n    integrity = _integrity_payload()\n    attr = _read_report("chase_attribution_report.json")',
            1,
        )
        text = text.replace(
            "    bucket_rows = []\n    if isinstance(buckets, dict):\n        for key, b in buckets.items():",
            "    trades_attr = _filter_chase_attributions(attr.get(\"trades\") or [], lane)\n    if lane and trades_attr:\n        buckets = {}\n        eff_rep = {\"buckets\": {}}\n        for key, b in (_chase_bucket_stats_from_trades(trades_attr) or {}).items():\n            buckets[key] = b\n    bucket_rows = []\n    if isinstance(buckets, dict):\n        for key, b in buckets.items():",
            1,
        )
        # add helper for dashboard-side bucket recompute
        text = text.replace(
            "def _chase_threshold_payload():",
            '''def _chase_bucket_stats_from_trades(rows):
    order = ["0", "1", "2", "3", "4", "5+"]
    buckets = {k: {"trades": 0, "wins": 0, "sum_pnl_usd": 0.0, "win_rate_pct": 0.0, "ev_usd": 0.0, "avg_hold_min": None} for k in order}
    for row in rows or []:
        if row.get("net_pnl_usd") is None and row.get("win") is None:
            continue
        try:
            cc = int(row.get("chase_count") or 0)
        except (TypeError, ValueError):
            cc = 0
        key = "5+" if cc >= 5 else str(cc)
        b = buckets[key]
        b["trades"] += 1
        pnl = float(row.get("net_pnl_usd") or 0)
        b["sum_pnl_usd"] = round(b["sum_pnl_usd"] + pnl, 2)
        if row.get("win") or pnl > 0:
            b["wins"] += 1
    for key, b in buckets.items():
        n = b["trades"]
        if n:
            b["win_rate_pct"] = round(100.0 * b["wins"] / n, 1)
            b["ev_usd"] = round(b["sum_pnl_usd"] / n, 2)
    return buckets


def _chase_threshold_payload(lane: str = ""):''',
            1,
        )
        text = text.replace(
            '        "delay_delta": delay.get("delta_chase_3plus_vs_continuous"),\n        "question": eff.get("question"),\n    }',
            '        "delay_delta": delay.get("delta_chase_3plus_vs_continuous"),\n        "question": eff.get("question"),\n        "lane_filter": lane or "combined",\n        "integrity": integrity,\n    }',
            1,
        )

    if '@app.route("/api/chase")\ndef api_chase():\n    return jsonify(_chase_payload())' in text:
        text = text.replace(
            '@app.route("/api/chase")\ndef api_chase():\n    return jsonify(_chase_payload())',
            '@app.route("/api/chase")\ndef api_chase():\n    lane = request.args.get("lane") or ""\n    return jsonify(_chase_payload(lane=lane))',
            1,
        )
        text = text.replace(
            '@app.route("/api/chase-threshold")\ndef api_chase_threshold():\n    return jsonify(_chase_threshold_payload())',
            '@app.route("/api/chase-threshold")\ndef api_chase_threshold():\n    lane = request.args.get("lane") or ""\n    return jsonify(_chase_threshold_payload(lane=lane))',
            1,
        )

    if "def _chase_threshold_payload(lane" in text and "integrity" not in text.split("_chase_threshold_payload")[1][:1200]:
        old_th = '''def _chase_threshold_payload(lane: str = ""):
    rep = _read_report("chase_threshold_report.json")
    rows = []
    for key, block in (rep.get("thresholds") or {}).items():
        if int((block or {}).get("trades") or 0):
            rows.append({"threshold": key, **(block or {})})
    return {
        "generated_at": rep.get("generated_at"),
        "question": rep.get("question"),
        "thresholds": rows,
    }'''
        new_th = '''def _chase_threshold_payload(lane: str = ""):
    rep = _read_report("chase_threshold_report.json")
    attr = _read_report("chase_attribution_report.json")
    rows = []
    if lane:
        trades_attr = _filter_chase_attributions(attr.get("trades") or [], lane)
        buckets = _chase_bucket_stats_from_trades(trades_attr)
        for key, block in buckets.items():
            if int((block or {}).get("trades") or 0):
                rows.append({"threshold": key, **(block or {})})
    else:
        for key, block in (rep.get("thresholds") or {}).items():
            if int((block or {}).get("trades") or 0):
                rows.append({"threshold": key, **(block or {})})
    return {
        "generated_at": rep.get("generated_at"),
        "question": rep.get("question") or "Per exact limit_chase_count bucket (0, 1, 2, 3, 4, 5+)",
        "thresholds": rows,
        "lane_filter": lane or "combined",
        "integrity": _integrity_payload(),
    }'''
        if old_th in text:
            text = text.replace(old_th, new_th, 1)

    # typeb payload probability table
    if '"probability_table"' not in text.split("_typeb_payload")[1][:800]:
        text = text.replace(
            '        "rules": rep.get("predictor_rules") or rep.get("rules") or [],\n    }',
            '        "rules": rep.get("predictor_rules") or rep.get("rules") or [],\n        "probability_table": rep.get("probability_table") or [],\n    }',
            1,
        )

    # ladder sim unrealistic
    if '"unrealistic_vs_actual"' not in text.split("_ladder_sim_payload")[1][:400]:
        text = text.replace(
            '        "profiles": rep.get("profiles") or [],\n    }',
            '        "profiles": rep.get("profiles") or [],\n        "integrity": _integrity_payload(),\n    }',
            1,
        )

    # JS: integrity banner, lane filter, loadLanes columns, typeb prob, chase avg hold
    if "integrity-banner" in text and "integrityBanner" not in text:
        text = text.replace(
            "  const stale = d.stale || {};\n  const banner = document.getElementById('stale-banner');",
            "  const integrity = d.integrity || {};\n  const iBanner = document.getElementById('integrity-banner');\n  if (iBanner) {\n    if (integrity.valid === false || integrity.report_status === 'INVALID') {\n      iBanner.style.display = 'block';\n      const fails = (integrity.failed_checks || []).map(c => `${c.check}: expected ${c.expected}, found ${c.found}`).join(' · ');\n      iBanner.innerHTML = '<strong>' + (integrity.banner || '⚠ REPORT INVALID') + '</strong> ' + fails;\n    } else {\n      iBanner.style.display = 'none';\n    }\n  }\n  const stale = d.stale || {};\n  const banner = document.getElementById('stale-banner');",
            1,
        )

    if "function chaseLaneQuery()" not in text:
        text = text.replace(
            "function laneQuery() { return showAllLanes ? '?all=1' : ''; }",
            "function laneQuery() { return showAllLanes ? '?all=1' : ''; }\nfunction chaseLaneQuery() {\n  const sel = document.getElementById('chase-lane-filter');\n  const lane = sel ? sel.value : '';\n  return lane ? '?lane=' + encodeURIComponent(lane) : '';\n}",
            1,
        )

    if "loadChase()" in text and "chaseLaneQuery()" not in text.split("async function loadChase")[1][:200]:
        text = text.replace(
            "  const r = await fetch('/api/chase');",
            "  const r = await fetch('/api/chase' + chaseLaneQuery());",
            1,
        )
        text = text.replace(
            "  document.getElementById('chase-body').innerHTML = (d.buckets||[]).map(b =>\n    `<tr><td>${b.bucket}</td><td>${b.trades}</td><td>${b.win_rate_pct}%</td><td>$${fmtUsd(b.sum_pnl_usd)}</td><td>$${fmtUsd(b.ev_usd)}</td></tr>`).join('');",
            "  document.getElementById('chase-body').innerHTML = (d.buckets||[]).map(b =>\n    `<tr><td>${b.bucket||b.threshold||''}</td><td>${b.trades||0}</td><td>${b.win_rate_pct??'n/a'}%</td><td>$${fmtUsd(b.sum_pnl_usd??b.pnl_usd??0)}</td><td>$${fmtUsd(b.ev_usd??b.ev??0)}</td><td>${b.avg_hold_min??'—'}</td></tr>`).join('') || '<tr><td colspan=\"6\">No chase bucket data</td></tr>';",
            1,
        )
        text = text.replace(
            "<th>Bucket</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></thead><tbody id=\"chase-body\">",
            "<th>Bucket</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Avg hold</th></thead><tbody id=\"chase-body\">",
            1,
        )
        text = text.replace(
            "  const r = await fetch('/api/chase-threshold');",
            "  const r = await fetch('/api/chase-threshold' + chaseLaneQuery());",
            1,
        )
        text = text.replace(
            "<th>Threshold</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></thead><tbody id=\"chase-threshold-body\">",
            "<th>Bucket</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Avg hold</th></thead><tbody id=\"chase-threshold-body\">",
            1,
        )
        text = text.replace(
            "    return `<tr class=\"${cls}\"><td>${t.threshold||''}</td><td>${t.trades||0}</td><td>${wr}%</td><td>$${fmtUsd(pnl)}</td><td>$${fmtUsd(ev)}</td></tr>`;",
            "    return `<tr class=\"${cls}\"><td>${t.threshold||''}</td><td>${t.trades||0}</td><td>${wr}%</td><td>$${fmtUsd(pnl)}</td><td>$${fmtUsd(ev)}</td><td>${t.avg_hold_min??'—'}</td></tr>`;",
            1,
        )

    # loadLanes - 6 columns for A160/AI_SCAN
    if "coordinator_rejects" not in text.split("async function loadLanes")[1][:1200]:
        old_lane_row = """    const chk = row.v2_checker_pass_sims || 0;
    const rej = row.v2_reject_counterfactual_sims || 0;
    return `<tr class=\"${cls}\"><td>${row.lane}</td><td>${row.approves ?? 0}</td><td>${row.trades}</td><td>${chk || '\\u2014'}</td><td>${rej || '\\u2014'}</td><td>${sh}${row.shadow_fill_pct ? ' ('+row.shadow_fill_pct+'%)' : ''}</td>"""
        new_lane_row = """    const chk = row.v2_checker_pass_sims || 0;
    const rej = row.v2_reject_counterfactual_sims || 0;
    const isScan = row.lane === 'AI_SCAN';
    const paper = isScan ? 0 : row.trades;
    const apprCell = isScan ? `${row.approves ?? 0} appr` : (row.approves ?? 0);
    const rejCell = isScan ? `R:${row.coordinator_rejects||0} S:${row.coordinator_skipped||0} T:${row.coordinator_timeouts||0}` : (rej || '\\u2014');
    return `<tr class=\"${cls}\"><td>${row.lane}</td><td>${apprCell}</td><td>${paper}</td><td>${isScan ? '\\u2014' : (chk || '\\u2014')}</td><td>${isScan ? '\\u2014' : (chk || '\\u2014')}</td><td>${rejCell}</td><td>${sh}${row.shadow_fill_pct ? ' ('+row.shadow_fill_pct+'%)' : ''}</td>"""
        if old_lane_row in text:
            text = text.replace(old_lane_row, new_lane_row, 1)

    if "typeb-prob-body" in text and "prob-body" not in text.split("loadTypeB")[1][:800] if "loadTypeB" in text else True:
        text = text.replace(
            "  document.getElementById('typeb-sep-body').innerHTML = (d.separators||[]).map(s =>",
            "  document.getElementById('typeb-prob-body').innerHTML = (d.probability_table||[]).map(p =>\n    `<tr><td>${p.dimension||''}</td><td>${p.bucket||''}</td><td>${p.trades||0}</td><td>${p.type_b_count||0}</td><td>${p.type_b_probability_pct??'n/a'}%</td><td>${p.wr_pct??'n/a'}%</td></tr>`\n  ).join('') || '<tr><td colspan=\"6\">Run analyzer for Type B discovery table.</td></tr>';\n  document.getElementById('typeb-sep-body').innerHTML = (d.separators||[]).map(s =>",
            1,
        )

    if "rec.finding" not in text:
        text = text.replace(
            "      `<li><b>${rec.exit_reason}</b> (${rec.priority}) — ${rec.action} <code>${rec.script_hint||''}</code></li>`",
            "      `<li><b>${rec.exit_reason}</b> (${rec.priority})<br/><em>Finding:</em> ${rec.finding||rec.action}<br/><em>Recommendation:</em> ${rec.recommendation||rec.action}<br/><em>Expected gain:</em> ${rec.expected_gain||'TBD'} <code>${rec.script_hint||''}</code></li>`",
            1,
        )

    if "unrealistic_vs_actual" not in text.split("loadLadderSim")[1][:600]:
        text = text.replace(
            "    const cls = delta != null && delta > 50 ? 'amber' : '';",
            "    const cls = p.unrealistic_vs_actual ? 'red' : (delta != null && delta > 50 ? 'amber' : '');",
            1,
        )
        text = text.replace(
            "    return `<tr class=\"${cls}\"><td>${p.profile_id||''}</td>",
            "    const unreal = p.unrealistic_vs_actual ? ' UNREALISTIC' : '';\n    return `<tr class=\"${cls}\"><td>${p.profile_id||''}${unreal}</td>",
            1,
        )

    if "chase-lane-filter" in text and "chase-lane-filter').addEventListener" not in text:
        text = text.replace(
            "document.getElementById('show-all-lanes').addEventListener('change'",
            "const clf = document.getElementById('chase-lane-filter');\nif (clf) clf.addEventListener('change', () => { loadChase(); loadChaseThreshold(); });\n\ndocument.getElementById('show-all-lanes').addEventListener('change'",
            1,
        )

    return text


def main():
    for path in (ANALYZER, DASHBOARD):
        if not path.is_file():
            raise SystemExit(f"Missing {path}")
    az = patch_analyzer(ANALYZER.read_text(encoding="utf-8"))
    ANALYZER.write_text(az, encoding="utf-8")
    print(f"Patched {ANALYZER}")
    db = patch_dashboard(DASHBOARD.read_text(encoding="utf-8"))
    DASHBOARD.write_text(db, encoding="utf-8")
    print(f"Patched {DASHBOARD}")
    if ROOT_ANALYZER.is_file():
        shutil.copy2(ANALYZER, ROOT_ANALYZER)
        print(f"Synced {ROOT_ANALYZER}")


if __name__ == "__main__":
    main()
