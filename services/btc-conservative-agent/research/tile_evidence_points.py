"""Per-tile research evidence points for the current collection epoch.

One report answers, per registered tile and from collected rows only:

* fill worlds: every submitted order classified FILLED / PARTIAL / NO_FILL /
  EXPIRED / ADMIN_CANCELLED / UNRESOLVED, plus tape counterfactuals for each
  unfilled order (market entry at the signal and at TTL expiry);
* did vs missed: executed trades next to unfilled and capacity-skipped
  opportunities, all measured on the same fixed-horizon tape yardstick;
* AI usefulness: AI-approved vs AI-rejected closed trades and AI-filtered vs
  rules-only totals, gated at n>=30;
* fresh collection: closes per hour, identity drift and unresolved orders;
* quarantine receipt: every excluded row with its reason;
* after-cost EV ranking over strategy exits, gated at n>=30 with a 95% CI.

Counterfactuals are computed from the observed 1s microstructure tape and are
labelled as such; anything the tape cannot observe is reported as a status,
never as a zero.  Nothing here can place, change or cancel an order.
"""
from __future__ import annotations

import ast
import bisect
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

REPORT_FILE = "tile_evidence_points_report.json"
REPORT_SCHEMA = "tile_evidence_points_v1"
MIN_RANK_SAMPLE = 30
CI_Z = 1.96
HOLD_HORIZONS_SEC = (1800, 3600)
HEADLINE_HOLD_SEC = 3600
TAPE_MAX_GAP_SEC = 10.0
DEFAULT_ENTRY_TTL_SEC = 1800.0
ENTRY_RECONCILIATION_ALLOWANCE_SEC = 180.0
TAPE_COVERAGE_GRACE_SEC = 300.0

# Exits and cancels issued by the deploy boundary (maintenance flatten) or an
# operator, not by the tile's own policy.  They stay in counts but never in EV.
FORCED_EXIT_REASONS = frozenset({
    "ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT", "CIRCUIT_BREAKER_ADMIN_MANUAL",
})
RELAY_INTERFERENCE_EXIT_REASONS = frozenset({"PHANTOM_CANCEL_BY_RELAY"})
EXPIRY_REASONS = frozenset({"SIGNAL_TTL_EXPIRED"})
AI_APPROVE = frozenset({"APPROVE", "STRONG_APPROVE", "SOFT_APPROVE"})
AI_REJECT = frozenset({"REJECT", "SOFT_REJECT"})

TRADES_FILE = "trades_3factor.csv"
EXPIRED_FILE = "expired_orders_3factor.csv"
OPPORTUNITY_FILE = "lane_opportunity_capture.jsonl"
LIFECYCLE_FILE = str(Path("v3") / "ledgers" / "lifecycle.jsonl")
AI_SCAN_FILE = "ai_reason_research.jsonl"
INTENT_AUDIT_FILE = "duplicate_intent_audit.jsonl"
TAPE_FILE = "market_microstructure_1s.jsonl"


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _epoch_seconds(value: Any) -> float | None:
    number = _finite(value)
    if number is not None:
        return number / 1000.0 if number > 1e12 else number
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _truthy(value: Any) -> bool:
    return value is True or _upper(value) in {"TRUE", "1", "YES"}


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    text = str(value or "").strip()
    if not text.startswith("{"):
        return {}
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(text)
        except (ValueError, SyntaxError, TypeError):
            continue
        if isinstance(parsed, Mapping):
            return parsed
    return {}


def exact_net_pnl(row: Mapping[str, Any]) -> tuple[float | None, str]:
    """Reconciled terminal net PnL; the CSV ``net_pnl_usd`` column is cent-rounded."""
    receipt = _mapping(row.get("execution_cost_accounting"))
    observed = _finite(receipt.get("observed_net_pnl_usd"))
    if receipt.get("reconciled") is True and observed is not None:
        return observed, "TERMINAL_COST_RECEIPT_EXACT"
    for key in ("net_pnl_usd", "outcome_net_pnl_usd"):
        recorded = _finite(row.get(key))
        if recorded is not None:
            return recorded, "RECORDED_CSV_VALUE"
    return None, "NET_PNL_MISSING"


def _summary(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if not n:
        return {"n": 0, "mean_usd": None, "total_usd": None, "win_rate": None,
                "stdev_usd": None, "ci95_usd": None}
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if n >= 2 else None
    ci = None
    if stdev is not None:
        half = CI_Z * stdev / math.sqrt(n)
        ci = [round(mean - half, 6), round(mean + half, 6)]
    return {"n": n, "mean_usd": round(mean, 6), "total_usd": round(sum(values), 6),
            "win_rate": round(sum(1 for v in values if v > 0) / n, 4),
            "stdev_usd": round(stdev, 6) if stdev is not None else None, "ci95_usd": ci}


def _gate(n: int) -> str:
    return "OK" if n >= MIN_RANK_SAMPLE else f"NOT_ENOUGH_DATA (n<{MIN_RANK_SAMPLE})"


class Tape:
    """Observed 1s BBO/last buckets; lookups never extrapolate past a gap or the end."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]):
        buckets = {}
        for row in rows or ():
            ts = _finite(row.get("bucket_ts"))
            if ts is None:
                continue
            bid, ask, last = (_finite(row.get(k)) for k in ("bid", "ask", "last"))
            mid = (bid + ask) / 2.0 if bid and ask and ask >= bid else last
            if not mid or mid <= 0:
                continue
            low = min(v for v in (last, ask, mid) if v)
            high = max(v for v in (last, bid, mid) if v)
            buckets[ts] = (mid, low, high)
        self.ts = sorted(buckets)
        self.mid = [buckets[t][0] for t in self.ts]
        self.low = [buckets[t][1] for t in self.ts]
        self.high = [buckets[t][2] for t in self.ts]

    @property
    def start(self) -> float | None:
        return self.ts[0] if self.ts else None

    @property
    def end(self) -> float | None:
        return self.ts[-1] if self.ts else None

    def mark(self, t: float) -> float | None:
        i = bisect.bisect_right(self.ts, t) - 1
        if i < 0 or t > self.ts[-1] or t - self.ts[i] > TAPE_MAX_GAP_SEC:
            return None
        return self.mid[i]


    def window(self, t0: float, t1: float) -> tuple[list[float], list[float]]:
        a, b = bisect.bisect_left(self.ts, t0), bisect.bisect_right(self.ts, t1)
        return self.low[a:b], self.high[a:b]


def fixed_horizon_outcome(tape: Tape, *, direction: str, entry_price: Any, entry_ts: Any,
                          leverage: Any, margin_usd: Any, hold_sec: float) -> dict[str, Any]:
    """Margin return of holding from ``entry_ts`` at ``entry_price`` for ``hold_sec``."""
    entry, ts = _finite(entry_price), _epoch_seconds(entry_ts)
    lev, margin = _finite(leverage), _finite(margin_usd)
    if direction not in {"LONG", "SHORT"} or not entry or ts is None or not lev or margin is None:
        return {"status": "INPUTS_INCOMPLETE"}
    if tape.start is None or ts < tape.start:
        return {"status": "TAPE_NOT_COVERED"}
    if ts + hold_sec > tape.end:
        return {"status": "HORIZON_INCOMPLETE"}
    exit_mid = tape.mark(ts + hold_sec)
    if exit_mid is None:
        return {"status": "TAPE_GAP"}
    sign = 1.0 if direction == "LONG" else -1.0
    ret = (exit_mid - entry) / entry * 100.0 * sign * lev
    lows, highs = tape.window(ts, ts + hold_sec)
    best = max(highs) if sign > 0 else min(lows)
    worst = min(lows) if sign > 0 else max(highs)
    return {
        "status": "COMPUTED",
        "margin_return_pct": round(ret, 4),
        "net_pnl_usd": round(margin * ret / 100.0, 6),
        "mfe_margin_pct": round((best - entry) / entry * 100.0 * sign * lev, 4),
        "mae_margin_pct": round((worst - entry) / entry * 100.0 * sign * lev, 4),
    }


def market_entry_outcome(tape: Tape, *, direction: str, anchor_ts: Any, leverage: Any,
                         margin_usd: Any) -> dict[str, Any]:
    """Enter at the tape mid at ``anchor_ts`` and hold for each horizon."""
    ts = _epoch_seconds(anchor_ts)
    entry = tape.mark(ts) if ts is not None else None
    if entry is None:
        status = "INPUTS_INCOMPLETE" if ts is None else "TAPE_NOT_COVERED"
        return {"horizons": {str(h): {"status": status} for h in HOLD_HORIZONS_SEC}}
    return {"entry_price": entry, "horizons": {
        str(h): fixed_horizon_outcome(tape, direction=direction, entry_price=entry, entry_ts=ts,
                                      leverage=leverage, margin_usd=margin_usd, hold_sec=h)
        for h in HOLD_HORIZONS_SEC}}


def _cf_summary(outcomes: list[Mapping[str, Any]]) -> dict[str, Any]:
    key = str(HEADLINE_HOLD_SEC)
    values, statuses = [], Counter()
    for outcome in outcomes:
        cell = (outcome.get("horizons") or {}).get(key) if "horizons" in outcome else outcome
        status = (cell or {}).get("status") or "NOT_COMPUTED"
        statuses[status] += 1
        pnl = _finite((cell or {}).get("net_pnl_usd"))
        if status == "COMPUTED" and pnl is not None:
            values.append(pnl)
    return {"rows": len(outcomes), "hold_sec": HEADLINE_HOLD_SEC,
            "status_counts": dict(sorted(statuses.items())), **_summary(values)}


def _tile_specs(registry: Mapping[str, Any], tile_order: Iterable[str]) -> dict[str, dict[str, Any]]:
    specs = {}
    for lane in tile_order:
        spec = registry.get(lane) or {}
        specs[str(lane).upper()] = {
            "label": spec.get("label") or lane,
            "margin_usd": _finite(spec.get("requested_margin_usd")),
        }
    return specs


def _ai_verdict(scan: Mapping[str, Any] | None) -> str:
    decision = _upper((scan or {}).get("ai_decision"))
    return "APPROVE" if decision in AI_APPROVE else "REJECT" if decision in AI_REJECT else "OTHER"


def classify_trade_rows(trades: Iterable[Mapping[str, Any]], *, tiles: set[str], epoch_id: str | None,
                        v2_start_ts: float | None, relay_interference_ids: set[str] = frozenset()):
    """Split closed trade rows into the current tile cohort and quarantined rows."""
    current, quarantined, seen = [], [], set()
    for row in trades or ():
        trade_id = str(row.get("trade_id") or "")
        if trade_id and trade_id in seen:
            quarantined.append((row, "DUPLICATE_TRADE_ID"))
            continue
        seen.add(trade_id)
        lane = _upper(row.get("research_lane"))
        row_epoch = str(row.get("epoch_id") or "").strip()
        closed_ts = _epoch_seconds(row.get("close_ts")) or _epoch_seconds(row.get("ts"))
        exit_reason = _upper(row.get("exit_reason") or row.get("outcome_exit_reason"))
        if lane not in tiles:
            reason = "LEGACY_CONTINUOUS_LANE" if lane == "CONTINUOUS" else "NON_REGISTRY_LANE"
        elif trade_id in relay_interference_ids or exit_reason in RELAY_INTERFERENCE_EXIT_REASONS:
            reason = "RELAY_INTERFERENCE_PHANTOM_CANCEL"
        elif epoch_id and row_epoch and row_epoch.lower() != "nan" and row_epoch != epoch_id:
            reason = "PRIOR_EPOCH"
        elif v2_start_ts and closed_ts is not None and closed_ts < v2_start_ts:
            reason = "PRE_CUTOVER"
        else:
            current.append(row)
            continue
        quarantined.append((row, reason))
    return current, quarantined


def build_tile_evidence_points(
    *, registry: Mapping[str, Any], tile_order: Iterable[str], trades: Iterable[Mapping[str, Any]],
    expired: Iterable[Mapping[str, Any]], opportunities: Iterable[Mapping[str, Any]],
    lifecycles: Iterable[Mapping[str, Any]], ai_scans: Iterable[Mapping[str, Any]],
    intent_audit: Iterable[Mapping[str, Any]], tape_rows: Iterable[Mapping[str, Any]],
    epoch_id: str | None, v2_start_ts: float | None, generated_at: float | None = None,
    relay_interference_ids: Iterable[str] = (),
) -> dict[str, Any]:
    tile_order = [str(lane).upper() for lane in tile_order]
    tiles = set(tile_order)
    specs = _tile_specs(registry, tile_order)
    tape = Tape(tape_rows)
    scans = {str(r.get("trade_id")): r for r in ai_scans or () if r.get("trade_id")}
    intent_scan = {str(r.get("trade_id")): str(r.get("shared_ai_call_id"))
                   for r in intent_audit or () if r.get("trade_id") and r.get("shared_ai_call_id")}

    def scan_for(trade_id: str, call_id: Any = None):
        return scans.get(str(call_id or "") or intent_scan.get(trade_id, ""))

    current, quarantined = classify_trade_rows(
        trades, tiles=tiles, epoch_id=epoch_id, v2_start_ts=v2_start_ts,
        relay_interference_ids=set(relay_interference_ids or ()))

    submitted: dict[str, dict[str, float | None]] = defaultdict(dict)
    filled_ids: dict[str, set[str]] = defaultdict(set)
    for row in opportunities or ():
        lane, trade_id, event = _upper(row.get("lane")), str(row.get("trade_id") or ""), row.get("event")
        if lane not in tiles or not trade_id:
            continue
        if event == "ORDER_SUBMITTED":
            submitted[lane].setdefault(trade_id, _epoch_seconds(row.get("ts")))
        elif event == "FILLED":
            filled_ids[lane].add(trade_id)

    lifecycle_nofill_terminals: Counter[str] = Counter()
    lifecycle_quarantine: Counter[str] = Counter()
    skipped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in lifecycles or ():
        lane = _upper(row.get("research_lane"))
        if lane == "CONTINUOUS":
            lifecycle_quarantine["LEGACY_CONTINUOUS_LANE"] += 1
            continue
        if lane not in tiles:
            if lane:
                lifecycle_quarantine[f"NON_TILE_POLICY:{lane}"] += 1
            continue
        if epoch_id and row.get("epoch_id") and row.get("epoch_id") != epoch_id:
            lifecycle_quarantine["PRIOR_EPOCH"] += 1
            continue
        if row.get("terminal_no_fill") is True or _upper(row.get("outcome_state")) == "NO_FILL":
            lifecycle_nofill_terminals[lane] += 1
        if _upper(row.get("observation_status")) == "NO_ORDER" and row.get("terminal"):
            skipped[lane].append(row)

    executed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pnl_basis: Counter[str] = Counter()
    signatures: dict[str, Counter[str]] = defaultdict(Counter)
    epochs: dict[str, Counter[str]] = defaultdict(Counter)
    direction_agreement: Counter[str] = Counter()
    rounding_zero_rows = 0
    for row in current:
        lane = _upper(row.get("research_lane"))
        pnl, basis = exact_net_pnl(row)
        pnl_basis[basis] += 1
        if _finite(row.get("net_pnl_usd")) == 0.0 and pnl not in (None, 0.0):
            rounding_zero_rows += 1
        signatures[lane][str(row.get("policy_signature") or "MISSING")] += 1
        epochs[lane][str(row.get("epoch_id") or "MISSING")] += 1
        direction = _upper(row.get("dir") or row.get("final_direction"))
        trade_id = str(row.get("trade_id") or "")
        scan = scan_for(trade_id, row.get("shared_ai_call_id"))
        if scan is not None:
            direction_agreement["AGREE" if _upper(scan.get("direction")) == direction else "DIFFER"] += 1
        exit_reason = _upper(row.get("exit_reason") or row.get("outcome_exit_reason"))
        entry_price = _finite(row.get("execution_entry_price")) or _finite(row.get("entry"))
        executed[lane].append({
            "trade_id": trade_id, "pnl": pnl,
            "forced_exit": exit_reason in FORCED_EXIT_REASONS,
            "close_ts": _epoch_seconds(row.get("close_ts")),
            "ai": _ai_verdict(scan) if scan is not None else "UNLINKED",
            "ai_same_direction": scan is not None and _upper(scan.get("direction")) == direction,
            "partial": _truthy(row.get("entry_partial_fill")),
            "cost": {k: _finite(row.get(k)) for k in ("taker_fees", "maker_fees", "funding_fees",
                                                        "entry_slippage_cost_usd", "exit_slippage_cost_usd")},
            "leverage": _finite(row.get("leverage")),
            "cf_actual_entry": fixed_horizon_outcome(
                tape, direction=direction, entry_price=entry_price, entry_ts=row.get("ts"),
                leverage=row.get("leverage"), margin_usd=row.get("margin_usdt"), hold_sec=HEADLINE_HOLD_SEC),
            "cf": market_entry_outcome(tape, direction=direction, anchor_ts=row.get("shared_ai_call_ts"),
                                       leverage=row.get("leverage"), margin_usd=row.get("margin_usdt")),
        })

    def tile_leverage(lane: str):
        values = Counter(t["leverage"] for t in executed.get(lane, []) if t["leverage"])
        return values.most_common(1)[0][0] if values else None

    unfilled: dict[str, list[dict[str, Any]]] = defaultdict(list)
    nofill_quarantine: Counter[str] = Counter()
    seen_orders: set[str] = set()
    for row in expired or ():
        lane = _upper(row.get("research_lane"))
        trade_id = str(row.get("trade_id") or "")
        if lane not in tiles:
            nofill_quarantine[f"NON_TILE_LANE:{lane or 'UNLABELLED'}"] += 1
            continue
        if trade_id in seen_orders or str(row.get("duplicate_of_trade_id") or "").strip():
            nofill_quarantine["DUPLICATE_ORDER_ROW"] += 1
            continue
        seen_orders.add(trade_id)
        created = _epoch_seconds(row.get("created_ts")) or _epoch_seconds(row.get("time"))
        if v2_start_ts and created is not None and created < v2_start_ts:
            nofill_quarantine["PRE_CUTOVER"] += 1
            continue
        reason = _upper(row.get("reason"))
        world = ("ADMIN_CANCELLED" if reason in FORCED_EXIT_REASONS
                 else "EXPIRED" if reason in EXPIRY_REASONS else "NO_FILL")
        direction = _upper(row.get("dir"))
        signal_ts = _epoch_seconds(row.get("shared_ai_call_ts")) or created
        scan = scan_for(trade_id, row.get("shared_ai_call_id"))
        unfilled[lane].append({
            "trade_id": trade_id, "world": world,
            "collector_touched": _truthy(row.get("touched_limit")),
            "ttl_outcome": row.get("no_fill_ttl_outcome"),
            "ai": _ai_verdict(scan) if scan is not None else "UNLINKED",
            "cf": market_entry_outcome(tape, direction=direction, anchor_ts=signal_ts,
                                       leverage=tile_leverage(lane), margin_usd=specs[lane]["margin_usd"]),
            "cf_at_expiry": market_entry_outcome(tape, direction=direction, anchor_ts=row.get("expired_ts"),
                                                 leverage=tile_leverage(lane), margin_usd=specs[lane]["margin_usd"]),
        })

    skipped_out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lane, rows in skipped.items():
        for row in rows:
            scan = scans.get(str(row.get("shared_ai_call_id") or ""))
            direction = _upper((scan or {}).get("direction"))
            signal_ts = _epoch_seconds((scan or {}).get("ts")) or _epoch_seconds(row.get("observed_ts"))
            skipped_out[lane].append({
                "reason": _upper(row.get("exact_reason")) or "UNSPECIFIED",
                "ai": _ai_verdict(scan) if scan is not None else "UNLINKED",
                "cf": market_entry_outcome(tape, direction=direction, anchor_ts=signal_ts,
                                           leverage=tile_leverage(lane), margin_usd=specs[lane]["margin_usd"]),
            })

    close_times = [t["close_ts"] for rows in executed.values() for t in rows if t["close_ts"]]
    data_end = max([v for v in [tape.end, *close_times] if v], default=None)
    generated = generated_at or datetime.now(timezone.utc).timestamp()
    horizon_end = data_end or generated
    window_h = (horizon_end - v2_start_ts) / 3600.0 if v2_start_ts and horizon_end > v2_start_ts else None

    fill_worlds, did_vs_missed, ai_usefulness, collection, ranking_rows = {}, {}, {}, {}, []
    for lane in tile_order:
        label = specs[lane]["label"]
        trades_l = executed.get(lane, [])
        unfilled_l = unfilled.get(lane, [])
        skipped_l = skipped_out.get(lane, [])
        orders = submitted.get(lane, {})
        closed_ids = {t["trade_id"] for t in trades_l}
        filled = filled_ids.get(lane, set()) | closed_ids
        worlds = Counter(u["world"] for u in unfilled_l)
        partial = sum(1 for t in trades_l if t["partial"])
        accounted = filled | {u["trade_id"] for u in unfilled_l}
        unresolved = [tid for tid in orders if tid not in accounted]
        cutoff = horizon_end - (DEFAULT_ENTRY_TTL_SEC + ENTRY_RECONCILIATION_ALLOWANCE_SEC)
        orphans = [tid for tid in unresolved if orders[tid] is not None and orders[tid] < cutoff]
        strategy_unfilled = [u for u in unfilled_l if u["world"] != "ADMIN_CANCELLED"]
        fill_worlds[lane] = {
            "label": label,
            "orders_submitted": len(orders),
            "worlds": {
                "FILLED": len(filled) - partial,
                "PARTIAL": partial,
                "NO_FILL": worlds.get("NO_FILL", 0),
                "EXPIRED": worlds.get("EXPIRED", 0),
                "ADMIN_CANCELLED": worlds.get("ADMIN_CANCELLED", 0),
                "UNRESOLVED_OR_RESTING": len(unresolved),
            },
            "expired_ttl_outcomes": dict(sorted(Counter(
                str(u["ttl_outcome"] or "UNKNOWN") for u in unfilled_l if u["world"] == "EXPIRED").items())),
            "open_positions_filled_not_closed": len(filled - closed_ids),
            "unfilled_counterfactual": {
                world: {
                    "market_at_signal": _cf_summary([u["cf"] for u in unfilled_l if u["world"] == world]),
                    "market_at_expiry": _cf_summary([u["cf_at_expiry"] for u in unfilled_l if u["world"] == world]),
                }
                for world in ("EXPIRED", "NO_FILL", "ADMIN_CANCELLED")
            },
            "collector_touched_limit": dict(sorted(Counter(
                "TOUCHED" if u["collector_touched"] else "NOT_TOUCHED" for u in strategy_unfilled).items())),
            "v3_lifecycle_no_fill_terminals": lifecycle_nofill_terminals.get(lane, 0),
        }
        strategy = [t for t in trades_l if not t["forced_exit"] and t["pnl"] is not None]
        forced = [t for t in trades_l if t["forced_exit"] and t["pnl"] is not None]
        all_closes = [t["pnl"] for t in trades_l if t["pnl"] is not None]
        did_vs_missed[lane] = {
            "label": label,
            "executed": {
                "actual_after_cost": _summary(all_closes),
                "actual_strategy_exits": _summary([t["pnl"] for t in strategy]),
                "actual_forced_exits": _summary([t["pnl"] for t in forced]),
                "fixed_horizon_from_actual_entry": _cf_summary([t["cf_actual_entry"] for t in trades_l]),
                "market_at_signal": _cf_summary([t["cf"] for t in trades_l]),
            },
            "missed_unfilled": _cf_summary([u["cf"] for u in strategy_unfilled]),
            "missed_unfilled_market_at_expiry": _cf_summary([u["cf_at_expiry"] for u in strategy_unfilled]),
            "missed_admin_cancelled": _cf_summary(
                [u["cf"] for u in unfilled_l if u["world"] == "ADMIN_CANCELLED"]),
            "skipped": {
                reason: _cf_summary([s["cf"] for s in skipped_l if s["reason"] == reason])
                for reason in sorted({s["reason"] for s in skipped_l})
            },
        }
        linked = [t for t in trades_l if t["pnl"] is not None and t["ai"] != "UNLINKED"]
        approved = [t["pnl"] for t in linked if t["ai"] == "APPROVE" and t["ai_same_direction"]]
        rejected = [t["pnl"] for t in linked if t["ai"] == "REJECT"]
        rules_only = sum(t["pnl"] for t in linked)
        ai_usefulness[lane] = {
            "label": label,
            "ai_approved_same_direction": {**_summary(approved), "gate": _gate(len(approved))},
            "ai_rejected": {**_summary(rejected), "gate": _gate(len(rejected))},
            "unlinked_trades": sum(1 for t in trades_l if t["ai"] == "UNLINKED"),
            "matched_trades": len(linked),
            "rules_only_total_usd": round(rules_only, 6) if linked else None,
            "ai_filtered_total_usd": round(sum(approved), 6) if linked else None,
            "ai_filtered_minus_rules_only_usd": round(sum(approved) - rules_only, 6) if linked else None,
            "gate": _gate(min(len(approved), len(rejected))),
            "gate_basis": "the smaller of the AI-approved and AI-rejected arms",
            "missed_by_ai_verdict": {
                verdict: _cf_summary([u["cf"] for u in strategy_unfilled if u["ai"] == verdict]
                                     + [s["cf"] for s in skipped_l if s["ai"] == verdict])
                for verdict in ("APPROVE", "REJECT")
            },
        }
        closes = sorted(t["close_ts"] for t in trades_l if t["close_ts"])
        recent = [ts for ts in closes if ts >= horizon_end - 7200]
        collection[lane] = {
            "label": label,
            "closed_current_epoch": len(trades_l),
            "strategy_exits": len(strategy),
            "forced_exits": len(forced),
            "first_close_utc": _iso(closes[0]) if closes else None,
            "last_close_utc": _iso(closes[-1]) if closes else None,
            "closes_per_hour_since_v2_start": round(len(closes) / window_h, 2) if window_h else None,
            "closes_per_hour_last_2h": round(len(recent) / 2.0, 2),
            "distinct_policy_signatures": dict(signatures.get(lane, {})),
            "distinct_epochs": dict(epochs.get(lane, {})),
            "identity_drift": len(signatures.get(lane, {})) > 1 or len(epochs.get(lane, {})) > 1,
            "unresolved_orders": len(unresolved),
            "orphan_orders_past_ttl": len(orphans),
            "orphan_order_ids": orphans[:20],
        }
        costs: dict[str, float] = defaultdict(float)
        for t in strategy:
            for key, value in t["cost"].items():
                if value is not None:
                    costs[key] += value
        stats = _summary([t["pnl"] for t in strategy])
        ranking_rows.append({
            "lane": lane, "label": label, **stats,
            "forced_exits_excluded": len(forced),
            "all_closes_descriptive": _summary(all_closes),
            "cost_components_usd": {k: round(v, 6) for k, v in sorted(costs.items())},
            "status": "RANKED" if stats["n"] >= MIN_RANK_SAMPLE else "NOT_ENOUGH_DATA",
            "closes_needed": max(0, MIN_RANK_SAMPLE - stats["n"]),
            "rank": None,
        })

    ranked = sorted((r for r in ranking_rows if r["status"] == "RANKED"), key=lambda r: r["mean_usd"], reverse=True)
    for position, row in enumerate(ranked, start=1):
        low, high = row["ci95_usd"] or (None, None)
        row["rank"] = position
        row["rank_confidence"] = ("PROVISIONAL_CI_SPANS_ZERO" if low is None or low <= 0 <= high
                                  else "PROVISIONAL_CI_EXCLUDES_ZERO")

    evidence_gaps = []
    if sum(len(v) for v in unfilled.values()) and not sum(lifecycle_nofill_terminals.values()):
        evidence_gaps.append({
            "code": "V3_LIFECYCLE_MISSING_TILE_NO_FILL_TERMINALS",
            "detail": "tile no-fills exist in the expired-orders ledger but the v3 lifecycle ledger has no "
                      "terminal NO_FILL rows for any tile; fill worlds here come from the expired-orders ledger",
        })
    if pnl_basis.get("RECORDED_CSV_VALUE"):
        evidence_gaps.append({"code": "EXACT_NET_PNL_RECEIPT_MISSING",
                              "detail": f"{pnl_basis['RECORDED_CSV_VALUE']} closes fell back to cent-rounded CSV PnL"})
    orphan_total = sum(c["orphan_orders_past_ttl"] for c in collection.values())
    if orphan_total:
        evidence_gaps.append({"code": "ORPHAN_ORDERS_PAST_TTL",
                              "detail": f"{orphan_total} submitted orders unresolved past TTL"})
    if any(c["identity_drift"] for c in collection.values()):
        evidence_gaps.append({"code": "IDENTITY_DRIFT",
                              "detail": "a tile has more than one policy signature or epoch"})
    if v2_start_ts and (tape.start is None or tape.start > v2_start_ts + TAPE_COVERAGE_GRACE_SEC):
        evidence_gaps.append({
            "code": "TAPE_COVERAGE_STARTS_AFTER_V2",
            "detail": ("no 1s microstructure tape" if tape.start is None else
                       f"the 1s tape starts at {_iso(tape.start)}, after the v2 start {_iso(v2_start_ts)}")
                      + "; counterfactuals before it are TAPE_NOT_COVERED",
        })

    return {
        "schema": REPORT_SCHEMA,
        "generated_at": _iso(generated),
        "epoch_id": epoch_id,
        "v2_data_start_utc": _iso(v2_start_ts),
        "data_end_utc": _iso(data_end),
        "tile_order": tile_order,
        "min_rank_sample": MIN_RANK_SAMPLE,
        "live_policy_change_allowed": False,
        "counterfactual_basis": {
            "tape": TAPE_FILE,
            "tape_start_utc": _iso(tape.start), "tape_end_utc": _iso(tape.end), "tape_buckets": len(tape.ts),
            "yardstick": "MARKET_AT_SIGNAL: enter at the 1s tape mid at the shared AI scan time in the tile's "
                         "direction and hold a fixed horizon; the same yardstick is applied to executed, unfilled "
                         "and skipped opportunities. Unfilled orders also get MARKET_AT_EXPIRY (cross the spread "
                         "when the TTL ran out). Limit-touch worlds are not recomputed because orders are chased "
                         "and only the final limit is recorded; the collector's touched_limit is reported instead",
            "hold_horizons_sec": list(HOLD_HORIZONS_SEC), "headline_hold_sec": HEADLINE_HOLD_SEC,
            "mark": "mid of the 1s bucket at entry+hold; gaps > 10s or horizons past the tape end are not computed",
            "costs": "fee profile BITFINEX_ZERO (0 maker/taker); funding not modelled in counterfactuals",
            "not_a_tile_exit_simulation": True,
        },
        "quarantine_receipt": {
            "trade_rows_current": len(current),
            "trade_rows_quarantined": len(quarantined),
            "trade_quarantine_by_reason": dict(sorted(Counter(reason for _r, reason in quarantined).items())),
            "trade_quarantine_rows": [
                {"trade_id": str(r.get("trade_id") or ""), "research_lane": _upper(r.get("research_lane")),
                 "epoch_id": r.get("epoch_id"), "close_ts": r.get("close_ts"), "reason": reason}
                for r, reason in quarantined[:200]
            ],
            "ev_ranking_exclusions": {
                "forced_exits_by_tile": {
                    lane: sum(1 for t in executed.get(lane, []) if t["forced_exit"]) for lane in tile_order},
                "reasons": sorted(FORCED_EXIT_REASONS),
                "policy": "deploy-boundary and operator exits stay in counts and did-vs-missed, never in EV ranking",
            },
            "no_fill_rows_quarantined": dict(sorted(nofill_quarantine.items())),
            "admin_cancelled_orders_by_tile": {
                lane: fill_worlds[lane]["worlds"]["ADMIN_CANCELLED"] for lane in tile_order},
            "lifecycle_rows_excluded": dict(sorted(lifecycle_quarantine.items())),
            "net_pnl_precision": {
                "basis_counts": dict(sorted(pnl_basis.items())),
                "csv_rounding_zero_rows": rounding_zero_rows,
                "policy": "the reconciled terminal cost receipt replaces the cent-rounded CSV net PnL",
            },
        },
        "fill_worlds": fill_worlds,
        "did_vs_missed": did_vs_missed,
        "ai_usefulness": ai_usefulness,
        "fresh_collection": {
            "tiles": collection,
            "window_hours_since_v2_start": round(window_h, 3) if window_h else None,
            "executed_direction_vs_ai_scan": dict(sorted(direction_agreement.items())),
        },
        "ev_ranking": {
            "basis": "strategy exits only; exact reconciled net PnL = gross at actual execution prices "
                     "(slippage embedded) minus trading fees and funding; 95% CI = mean +/- 1.96*sd/sqrt(n)",
            "min_sample": MIN_RANK_SAMPLE,
            "ranked_count": len(ranked),
            "rows": sorted(ranking_rows, key=lambda r: (r["rank"] or 99, tile_order.index(r["lane"]))),
        },
        "evidence_gaps": evidence_gaps,
    }


def _read_csv(path: str | None) -> list[dict[str, Any]]:
    if not path or not Path(path).is_file():
        return []
    csv.field_size_limit(1 << 30)
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def rotation_family(path: str | None) -> list[Path]:
    """The live file plus its numbered rotations (``x.jsonl.1`` ...), oldest first.

    The runtime rotates append ledgers in place, so after a rotation the live
    file holds only the newest rows. Sidecars such as ``x.jsonl.validation.json``
    are not rotations.
    """
    if not path:
        return []
    head = Path(path)
    rotations = []
    if head.parent.is_dir():
        for sibling in head.parent.glob(head.name + ".*"):
            suffix = sibling.name[len(head.name) + 1:]
            if suffix.isdigit() and sibling.is_file():
                rotations.append((int(suffix), sibling))
    family = [p for _, p in sorted(rotations, reverse=True)]
    return family + ([head] if head.is_file() else [])


def _read_jsonl(path: str | None, keep: Callable[[dict], bool] | None = None,
                project: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    rows = []
    for member in rotation_family(path):
        with open(member, encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(row, dict) and (keep is None or keep(row)):
                    rows.append({k: row.get(k) for k in project} if project else row)
    return rows


def load_evidence_inputs(resolve: Callable[[str], str | None]) -> dict[str, Any]:
    """Read every source ledger through the analyzer's data-path resolver."""
    return {
        "trades": _read_csv(resolve(TRADES_FILE)),
        "expired": _read_csv(resolve(EXPIRED_FILE)),
        "opportunities": _read_jsonl(resolve(OPPORTUNITY_FILE),
                                     lambda r: r.get("event") in {"ORDER_SUBMITTED", "FILLED"},
                                     ("lane", "trade_id", "event", "ts")),
        "lifecycles": _read_jsonl(resolve(LIFECYCLE_FILE), None, (
            "research_lane", "epoch_id", "terminal", "terminal_no_fill", "outcome_state",
            "observation_status", "exact_reason", "shared_ai_call_id", "observed_ts", "paper_policy_spec")),
        "ai_scans": _read_jsonl(resolve(AI_SCAN_FILE), None, ("trade_id", "ai_decision", "direction", "ts")),
        "intent_audit": _read_jsonl(resolve(INTENT_AUDIT_FILE), None, ("trade_id", "shared_ai_call_id")),
        "tape_rows": _read_jsonl(resolve(TAPE_FILE), None, ("bucket_ts", "bid", "ask", "last")),
    }