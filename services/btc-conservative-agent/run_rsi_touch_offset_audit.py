"""Offline 9h RSI + touch-offset audit. Read-only vs Fly mirror + public Bitfinex."""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from chase_offset_touch_grid import (
    CHASE_POLICIES,
    OFFSET_PCT_GRID,
    orig_limit_price,
    simulate_exits_on_1m,
    simulate_touch_fill,
)
from cycle_3m_indicators import (
    atr14_pct_of_price,
    candles_at_or_before,
    resample_1m_to_3m,
    rsi_closed_and_forming,
    wilder_rsi,
)

MIRROR = os.environ.get(
    "FLY_MIRROR",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "DoxxedCrypto", "fly-data-mirror"),
)
OUT_JSON = os.path.join(
    os.path.dirname(__file__),
    "rsi_touch_offset_audit.json",
)
WINDOW_H = 9.0
BITFINEX_1M = "https://api-pub.bitfinex.com/v2/candles/trade:1m:tBTCF0:USTF0/hist"
BITFINEX_5M = "https://api-pub.bitfinex.com/v2/candles/trade:5m:tBTCF0:USTF0/hist"
BITFINEX_5M_SPOT = "https://api-pub.bitfinex.com/v2/candles/trade:5m:tBTCUSD/hist"


def _parse_ts(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def _load_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _fetch_candles(url, start_ms, end_ms):
    out = []
    cursor = int(start_ms)
    end = int(end_ms)
    while cursor < end:
        q = f"{url}?limit=10000&sort=1&start={cursor}&end={end}"
        req = urllib.request.Request(q, headers={"User-Agent": "doxed-rsi-audit"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            chunk = json.loads(resp.read().decode("utf-8"))
        if not chunk:
            break
        out.extend(chunk)
        last = int(chunk[-1][0])
        if last <= cursor:
            break
        cursor = last + 1
        if len(chunk) < 500:
            break
    seen = {}
    for row in out:
        seen[int(row[0])] = row
    return [seen[k] for k in sorted(seen)]


def _native_5m_rsi(candles_5m, ts):
    available = [row for row in candles_5m if (row[0] / 1000.0) <= ts + 1e-9]
    if not available:
        return {"closed": None, "forming": None}
    last_open = available[-1][0] / 1000.0
    forming_closes = [float(row[4]) for row in available]
    if last_open + 300 <= ts + 1e-9:
        closed_closes = forming_closes
    else:
        closed_closes = forming_closes[:-1]
    return {"closed": wilder_rsi(closed_closes), "forming": wilder_rsi(forming_closes)}


def main():
    cycle = _load_jsonl(os.path.join(MIRROR, "cycle_3m_universe.jsonl"))
    snaps = _load_jsonl(os.path.join(MIRROR, "signal_snapshot.jsonl"))
    funnel = _load_jsonl(os.path.join(MIRROR, "execution_funnel.jsonl"))
    outcomes = _load_jsonl(os.path.join(MIRROR, "trade_outcome.jsonl"))
    trades = []
    tpath = os.path.join(MIRROR, "trades_3factor.csv")
    if os.path.isfile(tpath):
        with open(tpath, encoding="utf-8", newline="") as handle:
            trades = list(csv.DictReader(handle))

    last_ts = max(_parse_ts(r.get("captured_ts")) or 0 for r in cycle) if cycle else datetime.now(timezone.utc).timestamp()
    start_ts = last_ts - WINDOW_H * 3600
    rsi_none = sum(1 for r in cycle if r.get("rsi14") is None)
    rsi_3m_missing = sum(1 for r in cycle if r.get("rsi_3m") is None and r.get("rsi14") is None)

    taken = []
    seen_tid = set()
    for row in cycle:
        ts = _parse_ts(row.get("captured_ts"))
        if ts is None or ts < start_ts:
            continue
        if str(row.get("cycle_outcome") or "").upper() != "TAKEN":
            continue
        tid = row.get("trade_id")
        if not tid or tid in seen_tid:
            continue
        seen_tid.add(tid)
        taken.append(row)

    snap_by_id = {}
    for row in snaps:
        tid = row.get("trade_id")
        ts = _parse_ts(row.get("ts") or row.get("approve_ts"))
        if tid and ts and ts >= start_ts:
            snap_by_id[tid] = row

    funnel_by_id = defaultdict(list)
    for row in funnel:
        tid = row.get("trade_id")
        ts = _parse_ts(row.get("ts"))
        if tid and ts and ts >= start_ts - 3600:
            funnel_by_id[tid].append(row)

    trade_by_id = {r.get("trade_id"): r for r in trades if r.get("trade_id")}
    outcome_by_id = {r.get("trade_id"): r for r in outcomes if r.get("trade_id")}

    closed = []
    for tid, row in trade_by_id.items():
        ts = _parse_ts(row.get("close_ts") or row.get("ts"))
        if ts is None or ts < start_ts:
            # still include if signal in window
            snap = snap_by_id.get(tid) or {}
            sts = _parse_ts(snap.get("ts"))
            if sts is None or sts < start_ts:
                continue
        closed.append(row)
    closed.sort(key=lambda r: _parse_ts(r.get("ts") or r.get("close_ts")) or 0)

    filled_ids = set()
    ttl_ids = set()
    for tid, events in funnel_by_id.items():
        stages = {str(e.get("stage") or "").upper() for e in events}
        if "FILLED" in stages or "CLOSED" in stages:
            filled_ids.add(tid)
        if "ORDER_SUBMITTED" in stages and "FILLED" not in stages and "CLOSED" not in stages:
            ttl_ids.add(tid)

    warmup = 250 * 60
    candles_1m = _fetch_candles(BITFINEX_1M, int((start_ts - warmup) * 1000), int((last_ts + 7200) * 1000))
    candles_5m = _fetch_candles(BITFINEX_5M, int((start_ts - warmup) * 1000), int((last_ts + 3600) * 1000))
    candles_5m_spot = _fetch_candles(BITFINEX_5M_SPOT, int((start_ts - warmup) * 1000), int((last_ts + 3600) * 1000))

    rsi_table = []
    for row in closed:
        tid = row.get("trade_id")
        snap = snap_by_id.get(tid) or {}
        signal_ts = _parse_ts(snap.get("ts") or snap.get("approve_ts") or row.get("ts"))
        fill_ts = _parse_ts(row.get("ts"))
        ctx = snap.get("context") or {}
        univ = {}
        if isinstance(ctx, dict):
            univ = ctx.get("cycle_3m_universe") or ctx.get("exhaustion_3m") or {}
        logged_rsi = univ.get("rsi14") or univ.get("rsi_3m")
        cycle_match = next((c for c in cycle if c.get("trade_id") == tid), None)
        if logged_rsi is None and cycle_match:
            logged_rsi = cycle_match.get("rsi14")
        pack = {"trade_id": tid, "dir": row.get("dir") or snap.get("direction"), "logged_cycle_rsi14": logged_rsi}
        for label, ts in (("signal", signal_ts), ("fill", fill_ts)):
            if ts is None:
                pack[f"{label}_3m_closed"] = None
                continue
            r3 = rsi_closed_and_forming(candles_1m, ts=ts, bar_sec=180)
            r5 = rsi_closed_and_forming(candles_1m, ts=ts, bar_sec=300)
            n5 = _native_5m_rsi(candles_5m, ts)
            n5_spot = _native_5m_rsi(candles_5m_spot, ts)
            pack[f"{label}_3m_closed"] = r3["closed"]
            pack[f"{label}_3m_forming"] = r3["forming"]
            pack[f"{label}_5m_closed"] = r5["closed"]
            pack[f"{label}_5m_forming"] = r5["forming"]
            pack[f"{label}_5m_native_closed"] = n5["closed"]
            pack[f"{label}_5m_native_forming"] = n5["forming"]
            pack[f"{label}_5m_spot_closed"] = n5_spot["closed"]
            pack[f"{label}_5m_spot_forming"] = n5_spot["forming"]
            pack[f"{label}_5m_oversold_3m_not"] = (
                (r5["closed"] is not None and r5["closed"] < 30)
                and (r3["closed"] is None or r3["closed"] >= 30)
            )
            pack[f"{label}_native5m_oversold_3m_not"] = (
                (n5["closed"] is not None and n5["closed"] < 30)
                and (r3["closed"] is None or r3["closed"] >= 30)
            )
            pack[f"{label}_spot5m_oversold_3m_not"] = (
                (n5_spot["closed"] is not None and n5_spot["closed"] < 30)
                and (r3["closed"] is None or r3["closed"] >= 30)
            )
        pack["pnl_usd"] = _parse_ts(row.get("net_pnl_usd")) if False else (
            float(row["net_pnl_usd"]) if row.get("net_pnl_usd") not in (None, "") else None
        )
        try:
            pack["pnl_usd"] = float(row.get("net_pnl_usd"))
        except (TypeError, ValueError):
            pack["pnl_usd"] = None
        pack["exit_reason"] = row.get("exit_reason")
        pack["entry"] = row.get("entry")
        pack["signal_ts"] = signal_ts
        pack["fill_ts"] = fill_ts
        rsi_table.append(pack)

    # Offset grid on TAKEN/ORDER_SUBMITTED in window
    events = []
    for tid, snap in snap_by_id.items():
        fevents = funnel_by_id.get(tid) or []
        submitted = next((e for e in fevents if str(e.get("stage")).upper() == "ORDER_SUBMITTED"), None)
        if not submitted and str(snap.get("research_lane") or "").upper() == "AI_SCAN":
            continue
        price = None
        for src in (submitted, snap):
            if not src:
                continue
            for key in ("signal_price", "price", "planned_limit_price"):
                try:
                    val = float(src.get(key))
                except (TypeError, ValueError):
                    val = 0
                if val > 0 and key != "planned_limit_price":
                    price = val
                    break
            if price:
                break
        if not price:
            try:
                price = float(snap.get("price") or 0)
            except (TypeError, ValueError):
                price = 0
        if price <= 0:
            continue
        signal_ts = _parse_ts((submitted or snap).get("ts") or snap.get("approve_ts"))
        if signal_ts is None:
            continue
        direction = str(snap.get("direction") or "SHORT").upper()
        events.append({
            "trade_id": tid,
            "direction": direction,
            "signal_price": price,
            "signal_ts": signal_ts,
            "live_filled": tid in filled_ids or tid in trade_by_id,
            "atr14_pct": ((snap.get("context") or {}).get("cycle_3m_universe") or {}).get("atr14_pct_3m")
            or ((snap.get("context") or {}).get("exhaustion_3m") or {}).get("atr14_pct_3m"),
        })

    combo_pnl = defaultdict(lambda: {"thesis": 0.0, "atr": 0.0, "n_touch": 0, "n_green_thesis": 0, "n_red_thesis": 0})
    all_green = {}
    per_signal = []
    live_policy = "w234_s25_i180"

    for ev in events:
        atr = ev.get("atr14_pct")
        if atr is None:
            bars = resample_1m_to_3m(candles_at_or_before(candles_1m, ev["signal_ts"]))
            atr = atr14_pct_of_price(bars)
            ev["atr14_pct"] = atr
        signal_row = {"trade_id": ev["trade_id"], "direction": ev["direction"], "offsets": {}}
        for offset in OFFSET_PCT_GRID:
            # no-chase orig-only first stored; chase variants aggregated globally
            hit = simulate_touch_fill(
                candles_1m,
                signal_ts=ev["signal_ts"],
                signal_price=ev["signal_price"],
                direction=ev["direction"],
                offset_pct=offset,
                chase={"no_chase": True, "windows": set()},
            )
            exits = None
            if hit["touched"]:
                exits = simulate_exits_on_1m(
                    candles_1m,
                    direction=ev["direction"],
                    entry_price=hit["fill_price"],
                    fill_ts=hit["touch_ts"],
                    atr14_pct=atr,
                )
            signal_row["offsets"][str(offset)] = {
                "touched": hit["touched"],
                "touch_ts": hit["touch_ts"],
                "fill_price": hit["fill_price"],
                "limit": orig_limit_price(ev["signal_price"], ev["direction"], offset),
                "thesis_pnl": None if not exits else exits["thesis_or_ladder_4_2"].get("net_pnl_usd"),
                "atr_k1_pnl": None if not exits else (exits["atr_k1"] or {}).get("net_pnl_usd"),
                "thesis_green": None if not exits else exits["thesis_or_ladder_4_2"].get("green"),
            }
        per_signal.append(signal_row)

        for policy in CHASE_POLICIES:
            for offset in OFFSET_PCT_GRID:
                key = f"{offset:.2f}|{policy['id']}"
                hit = simulate_touch_fill(
                    candles_1m,
                    signal_ts=ev["signal_ts"],
                    signal_price=ev["signal_price"],
                    direction=ev["direction"],
                    offset_pct=offset,
                    chase=policy,
                )
                if not hit["touched"]:
                    all_green.setdefault(key, True)
                    continue
                exits = simulate_exits_on_1m(
                    candles_1m,
                    direction=ev["direction"],
                    entry_price=hit["fill_price"],
                    fill_ts=hit["touch_ts"],
                    atr14_pct=atr,
                )
                thesis_pnl = float(exits["thesis_or_ladder_4_2"]["net_pnl_usd"] or 0)
                atr_pnl = exits["atr_k1"].get("net_pnl_usd")
                combo_pnl[key]["thesis"] += thesis_pnl
                combo_pnl[key]["n_touch"] += 1
                if thesis_pnl > 0:
                    combo_pnl[key]["n_green_thesis"] += 1
                else:
                    combo_pnl[key]["n_red_thesis"] += 1
                    all_green[key] = False
                if atr_pnl is not None:
                    combo_pnl[key]["atr"] += float(atr_pnl)
                else:
                    all_green.setdefault(key, True)
            all_green.setdefault(f"unused", True)

    ranked = sorted(
        (
            {
                "key": key,
                "offset_pct": float(key.split("|")[0]),
                "chase": key.split("|")[1],
                "thesis_pnl": round(val["thesis"], 4),
                "atr_k1_pnl": round(val["atr"], 4),
                "n_touch": val["n_touch"],
                "n_green": val["n_green_thesis"],
                "n_red": val["n_red_thesis"],
                "all_touched_green": bool(all_green.get(key, True) and val["n_touch"] > 0 and val["n_red_thesis"] == 0),
            }
            for key, val in combo_pnl.items()
        ),
        key=lambda row: (row["thesis_pnl"], row["n_touch"]),
        reverse=True,
    )
    all_green_keys = [row["key"] for row in ranked if row["all_touched_green"]]
    best = ranked[0] if ranked else None

    oversold_3m = [r for r in rsi_table if (r.get("signal_3m_closed") is not None and r["signal_3m_closed"] < 30)
                   or (r.get("signal_3m_forming") is not None and r["signal_3m_forming"] < 30)]
    discrepancy = [
        r["trade_id"]
        for r in rsi_table
        if r.get("signal_5m_oversold_3m_not") or r.get("signal_native5m_oversold_3m_not")
        or r.get("fill_5m_oversold_3m_not") or r.get("fill_native5m_oversold_3m_not")
        or r.get("signal_spot5m_oversold_3m_not") or r.get("fill_spot5m_oversold_3m_not")
    ]

    report = {
        "schema": "rsi_touch_offset_audit_v1",
        "mirror": MIRROR,
        "window_hours": WINDOW_H,
        "window_start_ts": start_ts,
        "window_end_ts": last_ts,
        "window_start_iso": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
        "window_end_iso": datetime.fromtimestamp(last_ts, timezone.utc).isoformat(),
        "cycle_rows": len(cycle),
        "cycle_rsi14_null": rsi_none,
        "cycle_rsi_3m_field_missing_on_historical": True,
        "rsi_logging_broken": rsi_none > 0,
        "rsi_field_note": "Fly rows stamp rsi14 (forming 3m). rsi_3m alias + 5m diagnostic added going forward. Historical rsi14 was never null.",
        "taken_ids_in_window": len(taken),
        "funnel_filled_ids": len(filled_ids),
        "funnel_submitted_unfilled": len(ttl_ids),
        "closed_shorts": len(rsi_table),
        "candles_1m": len(candles_1m),
        "candles_5m_perp": len(candles_5m),
        "candles_5m_spot": len(candles_5m_spot),
        "rsi_series": "tBTCF0:USTF0 1m resampled; diagnostic native 5m perp + spot tBTCUSD",
        "rsi_table": rsi_table,
        "shorts_with_3m_rsi_lt_30_at_signal": [r["trade_id"] for r in oversold_3m],
        "tf_discrepancy_ids": discrepancy,
        "grid_signals": len(events),
        "best_combo_thesis": best,
        "all_touched_green_intersection": all_green_keys[:20],
        "all_touched_green_empty": len(all_green_keys) == 0,
        "top_10_combos": ranked[:10],
        "live_orig_unchanged_pct": 0.10,
        "per_signal_no_chase_offsets": per_signal,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({k: report[k] for k in report if k not in ("rsi_table", "per_signal_no_chase_offsets", "top_10_combos")}, indent=2))
    print("WROTE", OUT_JSON)
    print("CLOSED", len(rsi_table), "TAKEN", len(taken), "GRID_SIGNALS", len(events), "BEST", best)
    print("ALL_GREEN_EMPTY", report["all_touched_green_empty"], "n", len(all_green_keys))
    print("RSI_NULL", rsi_none, "DISCREPANCY", discrepancy)
    for row in rsi_table:
        print(
            row["trade_id"],
            "sig3c", row.get("signal_3m_closed"),
            "sig3f", row.get("signal_3m_forming"),
            "sig5c", row.get("signal_5m_closed"),
            "sig5n", row.get("signal_5m_native_closed"),
            "fill3c", row.get("fill_3m_closed"),
            "fill5c", row.get("fill_5m_closed"),
            "logged", row.get("logged_cycle_rsi14"),
            "pnl", row.get("pnl_usd"),
        )


if __name__ == "__main__":
    sys.exit(main() or 0)
