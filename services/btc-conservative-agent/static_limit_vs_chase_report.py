"""
STATIC_LIMIT_VS_CHASE_REPORT — A/B compare SR_MICRO_TILE_V2 vs SR_MICRO_TILE_V2_STATIC.

Compares fill rate, entry distance, MAE/MFE, WR, EV, hold time, Scenario C capture,
missed fills (TTL/CANCEL), and a coarse "profit lost to chase" proxy once enough
STATIC data exists. Until then emits INSUFFICIENT_DATA + KEEP collecting guidance.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean

REPORT_JSON = "STATIC_LIMIT_VS_CHASE_REPORT.json"
REPORT_TXT = "STATIC_LIMIT_VS_CHASE_REPORT.txt"
SHADOW_LANE_OUTCOME_FILE = "shadow_lane_outcome.jsonl"
LAB_LEDGER_FILE = "lane_lab_pnl_ledger.json"

LANE_V2 = "SR_MICRO_TILE_V2"
LANE_STATIC = "SR_MICRO_TILE_V2_STATIC"

MIN_CLOSES_FOR_VERDICT = 40
MIN_SPAWNS_FOR_FILL_RATE = 30


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _load_jsonl(path: str) -> list:
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _sf(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _lane_rows(rows: list, lane: str) -> list:
    lane_u = lane.upper()
    out = []
    for r in rows:
        if str(r.get("research_lane") or "").upper() == lane_u:
            out.append(r)
    return out


def _summarize_lane(rows: list, lane: str) -> dict:
    n = len(rows)
    filled = [r for r in rows if r.get("filled")]
    ttl = [r for r in rows if str(r.get("exit_reason") or r.get("entry_outcome") or "").upper() in ("TTL_EXPIRED", "NO_FILL")]
    cancelled = [r for r in rows if str(r.get("exit_reason") or r.get("entry_outcome") or "").upper() == "CANCELLED"]
    wins = [r for r in filled if _sf(r.get("net_pnl_usd"), 0) > 0]
    losses = [r for r in filled if _sf(r.get("net_pnl_usd"), 0) < 0]
    pnls = [_sf(r.get("net_pnl_usd"), 0.0) for r in filled]
    maes = [_sf(r.get("max_drawdown_margin_pct")) for r in filled if _sf(r.get("max_drawdown_margin_pct")) is not None]
    mfes = [_sf(r.get("max_profit_margin_pct")) for r in filled if _sf(r.get("max_profit_margin_pct")) is not None]
    holds = [_sf(r.get("fill_delay_sec")) for r in filled if _sf(r.get("fill_delay_sec")) is not None]
    # Entry distance: |fill - original_limit| / original_limit
    entry_dists = []
    for r in filled:
        lim = _sf(r.get("original_limit_price") or r.get("limit_price"))
        fill = _sf(r.get("fill_price") or r.get("entry"))
        if lim and lim > 0 and fill:
            entry_dists.append(abs(fill - lim) / lim * 100.0)
    scenario_c = [
        r for r in filled
        if "PROFIT_LOCK" in str(r.get("exit_reason") or "").upper()
        or "LADDER" in str(r.get("exit_reason") or "").upper()
    ]
    net = sum(pnls) if pnls else 0.0
    closes = len(filled)
    return {
        "lane": lane,
        "spawns": n,
        "fills": closes,
        "fill_rate_pct": round(100.0 * closes / n, 2) if n else 0.0,
        "ttl_expired": len(ttl),
        "cancelled": len(cancelled),
        "missed_fills": len(ttl) + len(cancelled),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / closes, 2) if closes else 0.0,
        "net_pnl_usd": round(net, 4),
        "ev_per_close": round(net / closes, 4) if closes else 0.0,
        "avg_mae_margin_pct": round(mean(maes), 4) if maes else None,
        "avg_mfe_margin_pct": round(mean(mfes), 4) if mfes else None,
        "avg_fill_delay_sec": round(mean(holds), 2) if holds else None,
        "avg_entry_dist_from_limit_pct": round(mean(entry_dists), 6) if entry_dists else None,
        "scenario_c_exits": len(scenario_c),
        "scenario_c_capture_pct": round(100.0 * len(scenario_c) / closes, 2) if closes else 0.0,
        "chase_modes": sorted({str(r.get("chase_mode") or "") for r in rows if r.get("chase_mode")}),
    }


def _lab_ledger_slice(path: str, lane: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("lanes") or {}).get(lane) or {}
    except Exception:
        return {}


def _recommendation(v2: dict, static: dict) -> dict:
    """KEEP / REMOVE / HYBRID with rationale. Prefer collecting until sample is enough."""
    s_closes = int(static.get("fills") or 0)
    v_closes = int(v2.get("fills") or 0)
    if s_closes < MIN_CLOSES_FOR_VERDICT or v_closes < MIN_CLOSES_FOR_VERDICT:
        return {
            "verdict": "KEEP_COLLECTING",
            "recommendation": "KEEP",
            "phase": "INSUFFICIENT_DATA",
            "why": (
                f"Need >={MIN_CLOSES_FOR_VERDICT} closes each side "
                f"(V2={v_closes}, STATIC={s_closes}). "
                "Do NOT kill V2 — STATIC is the right next experiment given V2 "
                "underperformance may be fill-location, not the micro S/R thesis. "
                "LIGHT_CHASE deferred to Phase 2."
            ),
            "action": (
                "Keep V2 FULL_CHASE collecting unchanged; keep STATIC shadow ON "
                "(toggle OFF / LAB). Re-run this report after both have enough closes."
            ),
        }

    # Enough data — compare EV + fill quality
    s_ev = float(static.get("ev_per_close") or 0)
    v_ev = float(v2.get("ev_per_close") or 0)
    s_mae = static.get("avg_mae_margin_pct")
    v_mae = v2.get("avg_mae_margin_pct")
    s_fill = float(static.get("fill_rate_pct") or 0)
    v_fill = float(v2.get("fill_rate_pct") or 0)
    ev_edge = s_ev - v_ev
    mae_better = (
        s_mae is not None and v_mae is not None and abs(float(s_mae)) < abs(float(v_mae))
    )

    if ev_edge >= 0.15 and s_fill >= max(20.0, v_fill * 0.7):
        return {
            "verdict": "PREFER_STATIC",
            "recommendation": "HYBRID",
            "phase": "READY",
            "why": (
                f"STATIC EV/close ${s_ev:.3f} beats V2 ${v_ev:.3f} "
                f"(Δ ${ev_edge:.3f}) with fill rate {s_fill:.1f}% vs {v_fill:.1f}%."
            ),
            "action": (
                "Promote STATIC as primary bracket entry; keep a small FULL_CHASE "
                "shadow for missed-fill telemetry. LIGHT_CHASE Phase 2 optional."
            ),
        }
    if ev_edge <= -0.15 and v_fill > s_fill:
        return {
            "verdict": "PREFER_V2_CHASE",
            "recommendation": "KEEP",
            "phase": "READY",
            "why": (
                f"V2 still wins on EV (${v_ev:.3f} vs ${s_ev:.3f}); STATIC fill rate "
                f"{s_fill:.1f}% may be starving the tile of trades."
            ),
            "action": (
                "Keep V2 FULL_CHASE. Consider LIGHT_CHASE (1 step) Phase 2 before "
                "killing STATIC — static may be too strict on TTL."
            ),
        }
    if mae_better and abs(ev_edge) < 0.15:
        return {
            "verdict": "HYBRID_FILL_QUALITY",
            "recommendation": "HYBRID",
            "phase": "READY",
            "why": (
                "EV similar, but STATIC shows better MAE (entry quality). "
                "Chase is not creating clear alpha; hybrid resting+light chase is next."
            ),
            "action": "Run LIGHT_CHASE Phase 2; keep both shadows until one wins CI.",
        }
    return {
        "verdict": "INCONCLUSIVE",
        "recommendation": "KEEP",
        "phase": "READY",
        "why": (
            f"No clear winner (ΔEV ${ev_edge:.3f}, STATIC fill {s_fill:.1f}% vs V2 {v_fill:.1f}%)."
        ),
        "action": "Keep both collecting; add LIGHT_CHASE Phase 2 for three-way compare.",
    }


def build_static_limit_vs_chase_report(cwd: str = None) -> dict:
    base = cwd or _agent_dir()
    shadow_path = os.path.join(base, SHADOW_LANE_OUTCOME_FILE)
    ledger_path = os.path.join(base, LAB_LEDGER_FILE)
    rows = _load_jsonl(shadow_path)
    v2_rows = _lane_rows(rows, LANE_V2)
    st_rows = _lane_rows(rows, LANE_STATIC)
    v2 = _summarize_lane(v2_rows, LANE_V2)
    static = _summarize_lane(st_rows, LANE_STATIC)
    v2["lab_ledger"] = _lab_ledger_slice(ledger_path, LANE_V2)
    static["lab_ledger"] = _lab_ledger_slice(ledger_path, LANE_STATIC)

    # Coarse proxy: if V2 fills farther from limit and loses more MAE, attribute
    # residual EV gap to chase/fill-location (not causal — research flag only).
    profit_lost_proxy = None
    if v2.get("fills") and static.get("fills"):
        profit_lost_proxy = {
            "note": (
                "Proxy only: (V2 EV - STATIC EV) * V2 closes when STATIC entry "
                "distance ≈ 0 and V2 entry distance > 0. Not causal."
            ),
            "v2_avg_entry_dist_pct": v2.get("avg_entry_dist_from_limit_pct"),
            "static_avg_entry_dist_pct": static.get("avg_entry_dist_from_limit_pct"),
            "ev_gap_usd_per_close": round(
                float(v2.get("ev_per_close") or 0) - float(static.get("ev_per_close") or 0), 4
            ),
            "implied_total_usd": round(
                (float(v2.get("ev_per_close") or 0) - float(static.get("ev_per_close") or 0))
                * int(v2.get("fills") or 0),
                4,
            ),
        }

    rec = _recommendation(v2, static)
    framing = {
        "why_static_not_kill_v2": (
            "V2 LAB is underperforming CONTINUOUS (85 closes, EV -$0.56) but CONTINUOUS "
            "chase research shows chase is not clearly killing alpha (4-chase / 5+ still "
            "positive EV). Fill location is the open question — STATIC isolates resting "
            "limit quality without discarding the micro S/R thesis or V2 collection."
        ),
        "phase_1_design": "STATIC + keep V2 FULL_CHASE (clean A/B). LIGHT_CHASE = Phase 2.",
        "v2_unchanged": True,
    }

    payload = {
        "schema": "static_limit_vs_chase_report_v1",
        "report_id": "STATIC_LIMIT_VS_CHASE_REPORT",
        "generated_at": _utc_now(),
        "lanes": {LANE_V2: v2, LANE_STATIC: static},
        "compare": {
            "delta_ev_static_minus_v2": round(
                float(static.get("ev_per_close") or 0) - float(v2.get("ev_per_close") or 0), 4
            ),
            "delta_fill_rate_pct": round(
                float(static.get("fill_rate_pct") or 0) - float(v2.get("fill_rate_pct") or 0), 2
            ),
            "delta_wr_pct": round(
                float(static.get("win_rate_pct") or 0) - float(v2.get("win_rate_pct") or 0), 2
            ),
            "profit_lost_to_chase_proxy": profit_lost_proxy,
        },
        "recommendation": rec,
        "framing": framing,
        "min_closes_for_verdict": MIN_CLOSES_FOR_VERDICT,
    }

    json_path = os.path.join(base, REPORT_JSON)
    txt_path = os.path.join(base, REPORT_TXT)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    lines = [
        "=" * 72,
        "STATIC_LIMIT_VS_CHASE_REPORT — V2 FULL_CHASE vs V2_STATIC",
        f"generated_at: {payload['generated_at']}",
        "=" * 72,
        "",
        f"Verdict: {rec.get('verdict')} · Recommendation: {rec.get('recommendation')}",
        f"Why: {rec.get('why')}",
        f"Action: {rec.get('action')}",
        "",
        "--- SR_MICRO_TILE_V2 (FULL_CHASE baseline) ---",
        json.dumps(v2, indent=2),
        "",
        "--- SR_MICRO_TILE_V2_STATIC (resting limit) ---",
        json.dumps(static, indent=2),
        "",
        "--- Compare ---",
        json.dumps(payload["compare"], indent=2),
        "",
        framing["why_static_not_kill_v2"],
        "",
    ]
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return payload


if __name__ == "__main__":
    out = build_static_limit_vs_chase_report()
    print(json.dumps({
        "verdict": out["recommendation"]["verdict"],
        "recommendation": out["recommendation"]["recommendation"],
        "v2_fills": out["lanes"][LANE_V2]["fills"],
        "static_fills": out["lanes"][LANE_STATIC]["fills"],
        "files": [REPORT_JSON, REPORT_TXT],
    }, indent=2))
