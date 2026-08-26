"""Compile normalized V3 evidence into protected, fail-closed candidates.

This is deliberately a staged evaluator. It does not materialize the nominal
billions of policy combinations. It evaluates a predeclared protection screen,
keeps one outcome per causal episode and policy, and leaves promotion blocked
until conservative execution and a sealed holdout exist.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from research_v3_contract import LADDERS, PARTIAL_TAKE_PROFIT_PLANS, canonical_hash
from research_v3_policy_replay import prepare_replay_price_path, replay_protected_policy
from research_v3_validation import validate_policy
from research.conservative_limit_fill import evaluate_limit_fill


def protection_screen() -> list[dict[str, Any]]:
    """Small, auditable Stage-1 safety screen requested by the user."""
    rows: list[dict[str, Any]] = []

    def add(name: str, *, family="FIXED_TARGET", mode="ATR_TARGET", atr_tp=2.5,
            atr_sl=None, thesis=None, thesis_sec=0, hard=30,
            time_stop=None, ladder="none", be_arm=None, be_floor=0,
            be_arm_atr=None,
            giveback_abs=None, giveback_fraction=None, atr_trail=None,
            chandelier=None, trail_activation=0, partial_plan="none") -> None:
        rows.append({
            "protection_id": name,
            "policy_family": family,
            "loss_protection": {
                "atr_stop_k": atr_sl,
                "thesis_cut_margin_pct": thesis,
                "thesis_window_sec": thesis_sec,
                "hard_stop_margin_pct": hard,
                "time_stop_min": time_stop,
            },
            "profit_protection": {
                "mode": mode,
                "atr_tp_k": atr_tp,
                "ladder": [list(rung) for rung in LADDERS[ladder]],
                "break_even_arm_mfe_pct": be_arm,
                "break_even_arm_atr_k": be_arm_atr,
                "break_even_floor_pct": be_floor,
                "mfe_giveback_abs_pct": giveback_abs,
                "mfe_giveback_fraction": giveback_fraction,
                "atr_trail_k": atr_trail,
                "chandelier_atr_k": chandelier,
                "trail_activation_atr_k": trail_activation,
                "partial_take_profits": [list(rung) for rung in PARTIAL_TAKE_PROFIT_PLANS[partial_plan]],
            },
        })

    for stop in (1.0, 1.5, 2.0):
        add(f"ATR_TP_2.5_ATR_SL_{stop:g}", atr_sl=stop)
    add("ATR_TP_2.5_THESIS_12_HARD_30", thesis=-12, thesis_sec=300)
    add("ATR_TP_2.5_SCENARIO_C", thesis=-12, thesis_sec=300, ladder="scenario_c")
    # Cross the Scenario C profit-lock ladder with volatility-sized initial
    # stops.  Previously these dimensions were screened independently, which
    # could not answer the user's profit-versus-drawdown question.  Keep the
    # legacy Scenario C row above as the no-ATR-stop control.
    for stop in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        add(
            f"ATR_TP_2.5_SCENARIO_C_ATR_SL_{stop:g}",
            atr_sl=stop,
            thesis=-12,
            thesis_sec=300,
            ladder="scenario_c",
        )
    for minutes in (30, 60, 90, 120):
        add(f"ATR_TP_2.5_TIME_{minutes}", time_stop=minutes)
    for arm in (2, 4, 6):
        for floor in (0, 1):
            add(f"ATR_TP_2.5_BE_{arm}_LOCK_{floor}", be_arm=arm, be_floor=floor)
    for giveback in (2, 4, 8):
        add(f"ATR_TP_2.5_GIVEBACK_{giveback}", giveback_abs=giveback)
    for fraction in (0.2, 0.4, 0.6):
        add(f"ATR_TP_2.5_GIVEBACK_{int(fraction * 100)}PCT", family="MFE_GIVEBACK", mode="MFE_GIVEBACK", atr_tp=None, giveback_fraction=fraction)
    for stop in (1.0, 1.5, 2.0):
        for activation in (0.75, 1.0, 1.25):
            for trail in (0.75, 1.0, 1.5):
                add(f"ATR_TRAIL_SL_{stop:g}_ARM_{activation:g}_TRAIL_{trail:g}", family="ATR_TRAIL", mode="ATR_TRAIL", atr_tp=None, atr_sl=stop, atr_trail=trail, trail_activation=activation)
    for chandelier in (1.5, 2.0, 2.5, 3.0):
        add(f"CHANDELIER_{chandelier:g}", family="CHANDELIER", mode="CHANDELIER", atr_tp=None, atr_sl=2.0, chandelier=chandelier, trail_activation=1.0)
    for plan in ("secure_25_25_runner", "secure_33_runner", "late_25_25_runner"):
        for trail in (0.75, 1.0, 1.5):
            add(f"HYBRID_{plan}_TRAIL_{trail:g}", family="HYBRID_RUNNER", mode="HYBRID_RUNNER", atr_tp=None, atr_sl=1.5, atr_trail=trail, trail_activation=1.0, partial_plan=plan)
    # Predeclared paper-only sibling of Patient Chase.  It retains the existing
    # entry family while adding a volatility-sized stop, partial realization,
    # ATR-based break-even arm, runner trail and final 2.5 ATR target.  Keeping
    # it as a distinct protection_id prevents mid-epoch policy contamination.
    add(
        "HYBRID_SL1_PT25_25_BE1.25_TRAIL1_TP2.5",
        family="HYBRID_RUNNER",
        mode="HYBRID_RUNNER",
        atr_tp=2.5,
        atr_sl=1.0,
        atr_trail=1.0,
        trail_activation=1.25,
        partial_plan="secure_25_25_runner",
        be_arm_atr=1.25,
        be_floor=0.5,
    )
    return rows


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def _load_segment(root: Path, ref: Mapping[str, Any]) -> list[dict[str, Any]]:
    relative = str(ref.get("relative_path") or "")
    if not relative:
        return []
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        return []
    try:
        envelope = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in (envelope.get("rows") or []) if isinstance(row, dict)]


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cycle_atr_by_event(root: Path) -> dict[str, float]:
    """Index the immutable 3-minute receipt used by pre-normalized V3.1 rows."""
    result: dict[str, float] = {}
    for row in _read_jsonl(root / "cycle_3m_universe.jsonl"):
        event_id = str(row.get("trade_id") or row.get("event_id") or "")
        atr = _number(row.get("atr14_pct_3m"))
        if event_id and atr is not None and atr > 0:
            result[event_id] = atr
    return result


def _normalized_entry_children(
    intent: Mapping[str, Any],
    executions: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize both multiverse and actual-paper V3.1 intent schemas."""
    children = [dict(row) for row in (intent.get("entry_children") or []) if isinstance(row, Mapping)]
    fill = next((
        row for row in sorted(executions, key=lambda value: _number(value.get("fill_ts")) or float("inf"))
        if _number(row.get("fill_ts")) is not None and _number(row.get("fill_price")) is not None
    ), None)
    if children:
        if fill is not None:
            for child in children:
                child.setdefault("fill_ts", fill.get("fill_ts"))
                child.setdefault("fill_price", fill.get("fill_price"))
                child.setdefault("fill_model", fill.get("fill_model") or "PAPER_OBSERVED")
        return children

    spec = intent.get("paper_policy_spec") if isinstance(intent.get("paper_policy_spec"), Mapping) else {}
    policy_id = str(
        spec.get("entry_limit_policy")
        or spec.get("policy_id")
        or intent.get("policy_id")
        or ""
    )
    if not policy_id:
        return []
    offset = _number(spec.get("entry_offset_fraction"))
    offset_pct = offset * 100.0 if offset is not None else None
    schedule = intent.get("chase_schedule") if isinstance(intent.get("chase_schedule"), Mapping) else {}
    intervals = [row for row in (schedule.get("intervals") or []) if isinstance(row, Mapping)]
    if offset_pct is None and intervals:
        offset_pct = _number(intervals[0].get("offset_pct"))
    if offset_pct is None:
        signal_price = _number(intent.get("signal_price"))
        limit_price = _number(intent.get("limit_price"))
        if signal_price and limit_price is not None:
            offset_pct = abs(limit_price - signal_price) / signal_price * 100.0
    return [{
        "entry_policy_id": policy_id,
        "offset_pct": offset_pct,
        "chase_id": policy_id,
        "fill_ts": fill.get("fill_ts") if fill is not None else None,
        "fill_price": fill.get("fill_price") if fill is not None else None,
        "fill_model": ((fill.get("fill_model") or "PAPER_OBSERVED") if fill is not None else None),
    }]


def _conservative_child_schedule(
    child: Mapping[str, Any], *, event_id: str,
) -> list[dict[str, Any]]:
    """Convert one counterfactual exact schedule to public-tape buckets.

    The simulator records fractional active boundaries.  The public evidence is
    one-second BBO/depth, so intermediate boundaries are assigned to the next
    generation and the final fractional second is retained by rounding its
    exclusive end upward.  This is the same causal convention used for actual
    finalized V3 paper schedules.
    """
    raw = [dict(row) for row in (child.get("chase_schedule") or []) if isinstance(row, Mapping)]
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        try:
            start_exact = float(row["active_from_ts"])
            end_exact = float(row["active_until_ts"])
            limit_price = float(row["limit_price"])
        except (KeyError, TypeError, ValueError):
            return []
        start_ts = math.floor(start_exact)
        end_ts = math.ceil(end_exact) if index == len(raw) - 1 else math.floor(end_exact)
        if end_ts <= start_ts or limit_price <= 0:
            return []
        normalized.append({
            "bucket_id": f"{event_id}:{child.get('entry_policy_id')}:{index}",
            "start_ts": start_ts,
            "end_ts": end_ts,
            "limit_price": limit_price,
            "generation": row.get("chase_step_index", index),
        })
    return normalized


def _conservative_child_receipt(
    source: Mapping[str, Any], child: Mapping[str, Any],
    *, microstructure_by_ts: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    schedule = _conservative_child_schedule(
        child, event_id=str(source.get("event_id") or "event"),
    )
    qty = _number(source.get("requested_qty"))
    if not schedule:
        return {
            "schema": "conservative_limit_fill_receipt_v1",
            "outcome": "UNSUPPORTED", "supported": False,
            "negative_reasons": ["COUNTERFACTUAL_CHASE_SCHEDULE_MISSING_OR_INVALID"],
        }
    if qty is None or qty <= 0:
        return {
            "schema": "conservative_limit_fill_receipt_v1",
            "outcome": "UNSUPPORTED", "supported": False,
            "negative_reasons": ["REQUESTED_QTY_MISSING_OR_INVALID"],
        }
    tape_rows: Iterable[Mapping[str, Any]] = source.get("ordered_1s_prices") or ()
    if microstructure_by_ts is not None:
        start = min(int(row["start_ts"]) for row in schedule) - 2
        end = max(int(row["end_ts"]) for row in schedule)
        tape_rows = (
            microstructure_by_ts[ts]
            for ts in range(start, end)
            if ts in microstructure_by_ts
        )
    receipt = evaluate_limit_fill(
        tape_rows,
        direction=str(source.get("direction") or "UNKNOWN"),
        requested_qty=qty,
        chase_schedule=schedule,
        symbol=str(source.get("market_microstructure_symbol") or "tBTCF0:USTF0"),
    )
    receipt.update({
        "event_id": source.get("event_id"),
        "episode_id": source.get("episode_id"),
        "entry_policy_id": child.get("entry_policy_id"),
        "chase_id": child.get("chase_id"),
        "evidence_world": "CONSERVATIVE_BBO_DEPTH_V1",
        "ideal_touch_reference": {
            "fill_ts": child.get("fill_ts"),
            "fill_price": child.get("fill_price"),
            "fill_model": child.get("fill_model"),
        },
    })
    return receipt


def load_candidate_inputs(
    data_dir: str | Path,
    *,
    epoch_id: str | None = None,
    minimum_signal_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Return one normalized event input per event, never per duplicate row."""
    root = Path(data_dir)
    ledgers = root / "v3" / "ledgers"
    def in_scope(row: Mapping[str, Any]) -> bool:
        if epoch_id is not None and str(row.get("epoch_id") or "") != str(epoch_id):
            return False
        return True

    opportunities = {}
    for row in _read_jsonl(ledgers / "opportunity.jsonl"):
        if not in_scope(row):
            continue
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            signal_ts = None
        if minimum_signal_ts is not None and (signal_ts is None or signal_ts < minimum_signal_ts):
            continue
        opportunities[str(row.get("episode_id"))] = row
    allowed_episodes = set(opportunities)
    intents = {
        str(row.get("event_id")): row
        for row in _read_jsonl(ledgers / "order_intent.jsonl")
        if in_scope(row) and str(row.get("episode_id") or "") in allowed_episodes
    }
    terminal = {
        str(row.get("event_id")): row for row in _read_jsonl(ledgers / "lifecycle.jsonl")
        if row.get("terminal") is True
        and in_scope(row)
        and str(row.get("episode_id") or "") in allowed_episodes
    }
    executions_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(ledgers / "execution.jsonl"):
        if not in_scope(row) or str(row.get("episode_id") or "") not in allowed_episodes:
            continue
        executions_by_event[str(row.get("event_id") or "")].append(row)
    cycle_atr = _cycle_atr_by_event(root)
    result = []
    for event_id in sorted(set(intents) & set(terminal)):
        intent, lifecycle = intents[event_id], terminal[event_id]
        episode_id = str(lifecycle.get("episode_id") or intent.get("episode_id") or "")
        if str(intent.get("episode_id") or "") != episode_id:
            continue
        executions = executions_by_event.get(event_id, [])
        if any(str(row.get("episode_id") or "") != episode_id for row in executions):
            continue
        one_second_rows = []
        one_minute_rows = []
        for ref in lifecycle.get("market_segment_refs") or []:
            if str(ref.get("timeframe")) == "1s":
                one_second_rows.extend(_load_segment(root, ref))
            elif str(ref.get("timeframe")) == "1m":
                one_minute_rows.extend(_load_segment(root, ref))
        feature = (opportunities.get(episode_id) or {}).get("feature_snapshot_at_signal") or {}
        market_context = feature.get("market_context") if isinstance(feature.get("market_context"), Mapping) else {}
        fill_execution = next((
            row for row in executions
            if _number(row.get("fill_ts")) is not None and _number(row.get("fill_price")) is not None
        ), {})
        atr14_pct = next((value for value in (
            _number(fill_execution.get("atr14_pct_at_fill")),
            _number(intent.get("atr14_pct_at_signal")),
            _number(intent.get("atr14_pct")),
            _number(feature.get("atr14_pct_3m")),
            cycle_atr.get(event_id),
        ) if value is not None and value > 0), None)
        result.append({
            "event_id": event_id,
            "episode_id": episode_id,
            "signal_ts": (opportunities.get(episode_id) or {}).get("signal_ts"),
            "regime": (
                feature.get("regime")
                or feature.get("market_regime")
                or market_context.get("regime_label")
                or "UNKNOWN"
            ),
            "direction": intent.get("executed_direction"),
            "atr14_pct": atr14_pct,
            "atr14_pct_basis": (
                "FILL_TIME_3M_ATR14" if _number(fill_execution.get("atr14_pct_at_fill")) is not None
                else "SIGNAL_TIME_3M_ATR14" if atr14_pct is not None
                else "UNAVAILABLE"
            ),
            "leverage": intent.get("leverage") or 100.0,
            "margin_usd": intent.get("margin_usd") or 0.25,
            "requested_qty": (
                (intent.get("execution_basis") or {}).get("requested_qty")
                or intent.get("requested_qty")
            ),
            "market_microstructure_symbol": (
                (intent.get("execution_basis") or {}).get("market_microstructure_symbol")
                or "tBTCF0:USTF0"
            ),
            "entry_children": _normalized_entry_children(intent, executions),
            "ordered_1s_prices": one_second_rows,
            "canonical_1m_ohlc": one_minute_rows,
            "terminal_outcome_state": lifecycle.get("outcome_state"),
        })
    return result


def _ordered_prices(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, float]]:
    result = []
    for row in rows:
        ts = row.get("ts", row.get("t"))
        price = row.get("price", row.get("mark", row.get("close")))
        try:
            result.append({"ts": float(ts), "price": float(price)})
        except (TypeError, ValueError):
            continue
    return sorted(result, key=lambda row: row["ts"])


def _conservative_ohlc_prices(rows: Iterable[Mapping[str, Any]], *, direction: str) -> list[dict[str, float]]:
    """Expand candles adverse-first when intrabar ordering is unknowable.

    This is deliberately pessimistic: if a stop and profit exit are both
    touched in one candle, the stop-side extreme appears first. The generated
    path is descriptive and never upgrades execution evidence to depth/tick.
    """
    result: list[dict[str, float]] = []
    for row in rows:
        try:
            ts = float(row.get("t", row.get("ts")))
            open_price = float(row.get("o", row.get("open")))
            high = float(row.get("h", row.get("high")))
            low = float(row.get("l", row.get("low")))
            close = float(row.get("c", row.get("close")))
        except (TypeError, ValueError):
            continue
        sequence = (open_price, low, high, close) if direction == "LONG" else (open_price, high, low, close)
        for offset, price in enumerate(sequence):
            result.append({"ts": ts + offset * 0.001, "price": price})
    return sorted(result, key=lambda row: row["ts"])


def evaluate_protection_screen(
    inputs: list[dict[str, Any]],
    *,
    sealed_holdout: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Evaluate exact policies; return descriptive rows and gated candidates."""
    episodes_by_policy: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    policy_specs: dict[str, dict[str, Any]] = {}
    protections = protection_screen()
    input_total = len(inputs)
    for source_index, source in enumerate(inputs, start=1):
        episode_id = str(source.get("episode_id") or "")
        if not episode_id:
            continue
        prices = _ordered_prices(source.get("ordered_1s_prices") or [])
        replay_path_basis = "CANONICAL_1S_ORDERED"
        if not prices:
            prices = _conservative_ohlc_prices(
                source.get("canonical_1m_ohlc") or [],
                direction=str(source.get("direction") or "UNKNOWN"),
            )
            replay_path_basis = "CANONICAL_1M_ADVERSE_FIRST_OHLC"
        microstructure_by_ts = {}
        for row in source.get("ordered_1s_prices") or []:
            try:
                bucket_ts = int(float(row.get("bucket_ts")))
            except (TypeError, ValueError):
                continue
            microstructure_by_ts[bucket_ts] = row
        for child in source.get("entry_children") or []:
            entry_id = str(child.get("entry_policy_id") or "")
            if not entry_id:
                continue
            conservative_receipt = _conservative_child_receipt(
                source, child, microstructure_by_ts=microstructure_by_ts,
            )
            conservative_outcome = str(conservative_receipt.get("outcome") or "UNSUPPORTED")
            conservative_fill_ts = conservative_receipt.get("trigger_bucket_ts")
            conservative_fill_price = conservative_receipt.get("fill_price")
            requested_qty = _number(conservative_receipt.get("requested_qty"))
            filled_qty = _number(conservative_receipt.get("filled_qty"))
            fill_fraction = (
                min(1.0, filled_qty / requested_qty)
                if requested_qty and filled_qty is not None else 0.0
            )
            prepared_price_path = None
            if conservative_fill_ts is not None and prices and source.get("atr14_pct") is not None:
                prepared_price_path = prepare_replay_price_path(
                    prices, fill_ts=float(conservative_fill_ts),
                )
            ideal_prepared_price_path = None
            if child.get("fill_ts") is not None and prices and source.get("atr14_pct") is not None:
                ideal_prepared_price_path = prepare_replay_price_path(
                    prices, fill_ts=float(child["fill_ts"]),
                )
            for protection in protections:
                policy_id = f"{entry_id}|{protection['protection_id']}"
                spec = {
                    "entry": {"entry_policy_id": entry_id, "offset_pct": child.get("offset_pct"), "chase_id": child.get("chase_id")},
                    "fill": {
                        "execution_world": "CONSERVATIVE_BBO_DEPTH_V1",
                        "source_fill_model": conservative_receipt.get("evaluator_version"),
                        "requested_qty": requested_qty,
                    },
                    "loss_protection": protection["loss_protection"],
                    "profit_protection": protection["profit_protection"],
                    "portfolio": {"concurrency_cap": 1, "size_scale": 1.0, "daily_loss_kill_pct": 3},
                }
                policy_specs[policy_id] = spec
                outcome: dict[str, Any]
                if conservative_outcome == "NO_FILL":
                    outcome = {
                        "outcome_state": "NO_FILL", "net_pnl_usd": None,
                        "fill_receipt": conservative_receipt,
                    }
                elif conservative_outcome == "UNSUPPORTED":
                    outcome = {
                        "outcome_state": "UNSUPPORTED",
                        "reason": "CONSERVATIVE_FILL_EVIDENCE_UNSUPPORTED",
                        "fill_receipt": conservative_receipt,
                    }
                elif conservative_outcome not in {"FILL", "PARTIAL_FILL"}:
                    outcome = {
                        "outcome_state": "UNSUPPORTED",
                        "reason": "UNKNOWN_CONSERVATIVE_FILL_OUTCOME",
                        "fill_receipt": conservative_receipt,
                    }
                elif not prices or source.get("atr14_pct") is None:
                    outcome = {"outcome_state": "UNSUPPORTED", "reason": "ORDERED_1S_PATH_OR_ATR_MISSING"}
                else:
                    replay = replay_protected_policy(
                        prices,
                        direction=str(source.get("direction") or "UNKNOWN"),
                        entry_price=float(conservative_fill_price or 0),
                        fill_ts=float(conservative_fill_ts),
                        atr_pct_at_fill=float(source["atr14_pct"]),
                        leverage=float(source.get("leverage") or 100),
                        margin_usd=float(source.get("margin_usd") or 0.25) * fill_fraction,
                        policy_spec=spec,
                        prepared_price_path=prepared_price_path,
                        collect_trace=False,
                    )
                    outcome = {
                        "outcome_state": (
                            "PARTIAL_FILL" if replay.get("status") == "COMPLETE" and conservative_outcome == "PARTIAL_FILL"
                            else "FULL_FILL" if replay.get("status") == "COMPLETE"
                            else str(replay.get("status") or "UNSUPPORTED")
                        ),
                        "net_pnl_usd": replay.get("net_pnl_usd"),
                        "exit_reason": replay.get("exit_reason"),
                        "profit_retention_ratio": replay.get("profit_retention_ratio"),
                        "profit_giveback_pct": replay.get("profit_giveback_pct"),
                        "underwater_observation_ratio": replay.get("underwater_observation_ratio"),
                        "replay_path_basis": replay_path_basis,
                        "fill_receipt": conservative_receipt,
                    }
                diagnostic_outcome: dict[str, Any]
                if child.get("fill_ts") is None:
                    diagnostic_outcome = {"outcome_state": "NO_FILL", "net_pnl_usd": None}
                elif not prices or source.get("atr14_pct") is None:
                    diagnostic_outcome = {
                        "outcome_state": "UNSUPPORTED",
                        "reason": "ORDERED_1S_PATH_OR_ATR_MISSING",
                    }
                else:
                    diagnostic_spec = {
                        **spec,
                        "fill": {
                            "execution_world": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
                            "source_fill_model": child.get("fill_model"),
                            "qualification_eligible": False,
                        },
                    }
                    diagnostic_replay = replay_protected_policy(
                        prices,
                        direction=str(source.get("direction") or "UNKNOWN"),
                        entry_price=float(child.get("fill_price") or 0),
                        fill_ts=float(child["fill_ts"]),
                        atr_pct_at_fill=float(source["atr14_pct"]),
                        leverage=float(source.get("leverage") or 100),
                        margin_usd=float(source.get("margin_usd") or 0.25),
                        policy_spec=diagnostic_spec,
                        prepared_price_path=ideal_prepared_price_path,
                        collect_trace=False,
                    )
                    diagnostic_outcome = {
                        "outcome_state": (
                            "FULL_FILL" if diagnostic_replay.get("status") == "COMPLETE"
                            else str(diagnostic_replay.get("status") or "UNSUPPORTED")
                        ),
                        "net_pnl_usd": diagnostic_replay.get("net_pnl_usd"),
                        "exit_reason": diagnostic_replay.get("exit_reason"),
                        "evidence_world": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
                        "qualification_eligible": False,
                    }
                # Multiple lane events from one AI call are correlated. A
                # deterministic event-id tie-break keeps one sample per episode.
                prior = episodes_by_policy[policy_id].get(episode_id)
                if prior is None or str(source.get("event_id")) < str(prior.get("source_event_id")):
                    episodes_by_policy[policy_id][episode_id] = {
                        "episode_id": episode_id,
                        "source_event_id": source.get("event_id"),
                        "signal_ts": source.get("signal_ts"),
                        "required_end_ts": (float(source.get("signal_ts") or 0) + 7200),
                        "regime": source.get("regime"),
                        "replay_path_basis": replay_path_basis,
                        "policy_outcomes": {policy_id: outcome},
                        "ideal_touch_policy_outcomes": {policy_id: diagnostic_outcome},
                    }

        if progress_callback is not None and (
            source_index == 1 or source_index == input_total or source_index % 5 == 0
        ):
            progress_callback({
                "phase": "PROTECTION_REPLAY",
                "input_events_completed": source_index,
                "input_events_total": input_total,
                "protection_variants": len(protections),
                "policies_materialized": len(episodes_by_policy),
            })

    assessed = []
    policies_tested = max(1, len(episodes_by_policy))
    for policy_id, by_episode in episodes_by_policy.items():
        rows = sorted(by_episode.values(), key=lambda row: float(row.get("signal_ts") or 0))
        holdout_start = int(len(rows) * 0.7)
        oos = rows[holdout_start:]
        prevalidation_outcomes = [
            (row.get("policy_outcomes") or {}).get(policy_id) or {}
            for row in oos
        ]
        prevalidation_states = {
            str(outcome.get("outcome_state") or "UNSUPPORTED")
            for outcome in prevalidation_outcomes
        }
        conservative_execution_ready = bool(oos) and bool(
            prevalidation_states & {"FULL_FILL", "PARTIAL_FILL"}
        ) and prevalidation_states <= {"FULL_FILL", "PARTIAL_FILL", "NO_FILL"}
        validation = validate_policy(
            oos,
            policy_id=policy_id,
            starting_equity_usd=1000,
            max_drawdown_usd=50,
            max_drawdown_pct=5,
            min_cvar95_usd=-10,
            policies_tested=policies_tested,
            conservative_execution=conservative_execution_ready,
            neighborhood_stable=False,
            sealed_holdout=sealed_holdout,
            liquidation_buffer_verified=False,
        )
        risk = validation["risk"]
        diagnostic_rows = [
            {
                **row,
                "policy_outcomes": row.get("ideal_touch_policy_outcomes") or {},
            }
            for row in oos
        ]
        diagnostic_validation = validate_policy(
            diagnostic_rows,
            policy_id=policy_id,
            starting_equity_usd=1000,
            max_drawdown_usd=50,
            max_drawdown_pct=5,
            min_cvar95_usd=-10,
            policies_tested=policies_tested,
            conservative_execution=False,
            neighborhood_stable=False,
            sealed_holdout=False,
            liquidation_buffer_verified=False,
        )
        replay_outcomes = [
            (row.get("policy_outcomes") or {}).get(policy_id) or {}
            for row in oos
        ]
        retentions = [float(row["profit_retention_ratio"]) for row in replay_outcomes if row.get("profit_retention_ratio") is not None]
        givebacks = [float(row["profit_giveback_pct"]) for row in replay_outcomes if row.get("profit_giveback_pct") is not None]
        underwater = [float(row["underwater_observation_ratio"]) for row in replay_outcomes if row.get("underwater_observation_ratio") is not None]
        outcome_states = validation.get("outcome_states") or {}
        full_fills = int(outcome_states.get("FULL_FILL", 0))
        partial_fills = int(outcome_states.get("PARTIAL_FILL", 0))
        no_fills = int(outcome_states.get("NO_FILL", 0))
        unsupported = sum(
            int(count) for state, count in outcome_states.items()
            if state not in {"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "NO_TRADE", "REJECTED", "REALIZED_ZERO_PNL"}
        )
        regime_breakdown = {}
        for regime in sorted({str(row.get("regime") or "UNKNOWN") for row in oos}):
            regime_rows = [row for row in oos if str(row.get("regime") or "UNKNOWN") == regime]
            regime_pnls = [
                float(((row.get("policy_outcomes") or {}).get(policy_id) or {}).get("net_pnl_usd"))
                for row in regime_rows
                if ((row.get("policy_outcomes") or {}).get(policy_id) or {}).get("net_pnl_usd") is not None
            ]
            regime_breakdown[regime] = {
                "independent_episodes": len(regime_rows),
                "scored_episodes": len(regime_pnls),
                "net_pnl_usd": round(sum(regime_pnls), 8),
                "expectancy_usd": round(sum(regime_pnls) / len(regime_pnls), 8) if regime_pnls else None,
            }
        assessed.append({
            "policy_id": policy_id,
            "policy_signature": canonical_hash("v3-policy", policy_specs[policy_id]),
            "policy_spec": policy_specs[policy_id],
            "policy_family": next((p["policy_family"] for p in protections if policy_id.endswith("|" + p["protection_id"])), "UNKNOWN"),
            "episodes_total": len(rows),
            "oos_episodes": len(oos),
            "supported_conservative_episodes": full_fills + partial_fills + no_fills,
            "full_fills": full_fills,
            "partial_fills": partial_fills,
            "no_fills": no_fills,
            "unsupported_episodes": unsupported,
            "conservative_fill_rate": (
                round((full_fills + partial_fills) / (full_fills + partial_fills + no_fills), 8)
                if full_fills + partial_fills + no_fills else None
            ),
            "evidence_world": "CONSERVATIVE_BBO_DEPTH_V1",
            "ideal_touch_diagnostic": {
                "evidence_world": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
                "qualification_eligible": False,
                "oos_net_usd": diagnostic_validation["risk"].get("net_pnl_usd"),
                "max_drawdown_usd": diagnostic_validation["risk"].get("max_drawdown_usd"),
                "expectancy_lcb_usd": diagnostic_validation["bootstrap"].get("mean_lcb95"),
                "outcome_states": diagnostic_validation.get("outcome_states"),
                "touches": int((diagnostic_validation.get("outcome_states") or {}).get("FULL_FILL", 0))
                + int((diagnostic_validation.get("outcome_states") or {}).get("PARTIAL_FILL", 0)),
                "no_touches": int((diagnostic_validation.get("outcome_states") or {}).get("NO_FILL", 0)),
                "wins": int(diagnostic_validation["risk"].get("wins") or 0),
                "losses": int(diagnostic_validation["risk"].get("losses") or 0),
            },
            "sealed_oos_net_usd": risk.get("net_pnl_usd"),
            "max_drawdown_usd": risk.get("max_drawdown_usd"),
            "cvar95_usd": risk.get("cvar95_usd"),
            "expectancy_lcb_usd": validation["bootstrap"].get("mean_lcb95"),
            "mean_profit_retention_ratio": round(sum(retentions) / len(retentions), 8) if retentions else None,
            "mean_profit_giveback_pct": round(sum(givebacks) / len(givebacks), 8) if givebacks else None,
            "mean_underwater_observation_ratio": round(sum(underwater) / len(underwater), 8) if underwater else None,
            "max_underwater_episodes": risk.get("max_underwater_episodes"),
            "regime_breakdown": regime_breakdown,
            "replay_path_bases": sorted({str(row.get("replay_path_basis") or "UNKNOWN") for row in rows}),
            "gates": validation["gates"],
            "validation": validation,
        })
    globally_ranked = sorted(assessed, key=lambda row: (
        -float(row.get("sealed_oos_net_usd") or 0),
        abs(float(row.get("max_drawdown_usd") or 0)),
        str(row["policy_id"]),
    ))

    def _family_balanced(rows: list[dict[str, Any]], *, cap: int = 2) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        selected = []
        for global_rank, row in enumerate(rows, start=1):
            family = str(row.get("policy_family") or "UNKNOWN")
            if counts[family] >= cap:
                continue
            counts[family] += 1
            selected.append({**row, "global_rank": global_rank, "family_rank": counts[family]})
            if len(selected) >= 100:
                break
        return selected

    profitable_conservative = _family_balanced([
        row for row in globally_ranked
        if int(row.get("full_fills") or 0) + int(row.get("partial_fills") or 0) > 0
        and isinstance(row.get("sealed_oos_net_usd"), (int, float))
        and float(row["sealed_oos_net_usd"]) > 0
    ])
    diagnostic_ranked = sorted(
        [
            row for row in assessed
            if int((row.get("ideal_touch_diagnostic") or {}).get("touches") or 0) > 0
            and isinstance((row.get("ideal_touch_diagnostic") or {}).get("oos_net_usd"), (int, float))
            and float((row.get("ideal_touch_diagnostic") or {})["oos_net_usd"]) > 0
        ],
        key=lambda row: (
            -float((row.get("ideal_touch_diagnostic") or {}).get("oos_net_usd") or 0),
            abs(float((row.get("ideal_touch_diagnostic") or {}).get("max_drawdown_usd") or 0)),
            str(row["policy_id"]),
        ),
    )
    profitable_ideal_touch_diagnostic = _family_balanced(diagnostic_ranked)
    # Keep the exhaustive assessed grid intact, but prevent small entry
    # variations of one protection family from saturating the public list.
    descriptive_family_cap = 2
    family_counts: dict[str, int] = defaultdict(int)
    descriptive = []
    for global_rank, row in enumerate(globally_ranked, start=1):
        family = str(row.get("policy_family") or "UNKNOWN")
        if family_counts[family] >= descriptive_family_cap:
            continue
        family_counts[family] += 1
        descriptive.append({
            **row,
            "global_rank": global_rank,
            "family_rank": family_counts[family],
        })
        if len(descriptive) >= 100:
            break
    family_leaders = {}
    for family in sorted({row["policy_family"] for row in assessed}):
        family_rows = [row for row in assessed if row["policy_family"] == family]
        family_leaders[family] = sorted(family_rows, key=lambda row: (-float(row.get("sealed_oos_net_usd") or 0), abs(float(row.get("max_drawdown_usd") or 0)), str(row["policy_id"])))[:10]
    dynamic_regime_leaders = {}
    regimes = sorted({regime for row in assessed for regime in row.get("regime_breakdown", {})})
    for regime in regimes:
        eligible = [row for row in assessed if (row.get("regime_breakdown") or {}).get(regime, {}).get("scored_episodes", 0) > 0]
        dynamic_regime_leaders[regime] = sorted(
            eligible,
            key=lambda row: (-float(row["regime_breakdown"][regime].get("net_pnl_usd") or 0), abs(float(row.get("max_drawdown_usd") or 0)), str(row["policy_id"])),
        )[:10]
    scenario_c_rows = [
        row for row in assessed
        if "|ATR_TP_2.5_SCENARIO_C" in str(row.get("policy_id") or "")
    ]
    scenario_c_by_stop: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scenario_c_by_chase_stop: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in scenario_c_rows:
        protection_id = str(row.get("policy_id") or "").split("|", 1)[-1]
        stop_label = (
            # A composed policy identity can append another ``|<exit>`` after
            # the Scenario-C protection.  Keep that suffix out of the numeric
            # stop key or the report sorter attempts float("1.5|ATR_TP...").
            protection_id.rsplit("_ATR_SL_", 1)[-1].split("|", 1)[0]
            if "_ATR_SL_" in protection_id
            else "CONTROL_NO_ATR_STOP"
        )
        scenario_c_by_stop[stop_label].append(row)
        entry_spec = (row.get("policy_spec") or {}).get("entry") or {}
        chase_label = str(
            entry_spec.get("chase_id")
            or entry_spec.get("entry_policy_id")
            or "UNKNOWN"
        )
        scenario_c_by_chase_stop[chase_label][stop_label].append(row)
    scenario_c_sort_key = lambda row: (
        -float(row.get("sealed_oos_net_usd") or 0),
        abs(float(row.get("max_drawdown_usd") or 0)),
        -float(row.get("expectancy_lcb_usd") or float("-inf")),
        str(row.get("policy_id") or ""),
    )
    scenario_c_atr_stop_sweep = {
        "qualification": "DESCRIPTIVE_ONLY",
        "warning": (
            "Conservative BBO/depth replay comparison; no stop or entry combination "
            "is qualified without sufficient supported execution and sealed OOS gates."
        ),
        "policies_tested": len(scenario_c_rows),
        "leaders_by_stop": {
            stop: sorted(rows, key=scenario_c_sort_key)[:5]
            for stop, rows in sorted(
                scenario_c_by_stop.items(),
                key=lambda item: (
                    item[0] == "CONTROL_NO_ATR_STOP",
                    float(item[0]) if item[0] != "CONTROL_NO_ATR_STOP" else float("inf"),
                ),
            )
        },
        # Persist one best row for every observed chase/stop cell.  The global
        # leaders are often saturated by no-chase variants; without this grid a
        # weaker (or negative) chased candidate is invisible and the dashboard
        # cannot answer whether chase + Scenario C + a stop actually worked.
        "best_by_chase_and_stop": {
            chase: {
                stop: sorted(rows, key=scenario_c_sort_key)[0]
                for stop, rows in sorted(
                    stop_rows.items(),
                    key=lambda item: (
                        item[0] == "CONTROL_NO_ATR_STOP",
                        float(item[0])
                        if item[0] != "CONTROL_NO_ATR_STOP"
                        else float("inf"),
                    ),
                )
                if rows
            }
            for chase, stop_rows in sorted(scenario_c_by_chase_stop.items())
        },
        "overall_leaders": sorted(scenario_c_rows, key=scenario_c_sort_key)[:25],
    }
    return {
        "schema": "safe_policy_candidate_screen_v3",
        "stage": "STAGE_1_PROTECTION_SCREEN",
        "input_events": len(inputs),
        "unique_policies_evaluated": len(assessed),
        "protection_variants": len(protections),
        "candidates": assessed,
        "descriptive_top_100": descriptive,
        "profitable_conservative_top_100": profitable_conservative,
        "profitable_ideal_touch_diagnostic_top_100": profitable_ideal_touch_diagnostic,
        "descriptive_selection": {
            "method": "GLOBAL_RANK_THEN_FAMILY_CAP",
            "per_family_cap": descriptive_family_cap,
            "families_represented": len(family_counts),
            "rows_displayed": len(descriptive),
            "globally_ranked_policies": len(globally_ranked),
            "note": "The exhaustive policy grid is unchanged; only the public shortlist is family-balanced.",
        },
        "profit_capture_leaders": family_leaders,
        "drawdown_control_leaders": sorted(assessed, key=lambda row: (abs(float(row.get("max_drawdown_usd") or 0)), -float(row.get("sealed_oos_net_usd") or 0)))[:25],
        "dynamic_regime_leaders": dynamic_regime_leaders,
        "scenario_c_atr_stop_sweep": scenario_c_atr_stop_sweep,
        "warning": "Descriptive rows use conservative BBO/depth entry receipts but remain unqualified until all OOS and safety gates pass. Ideal-touch references are diagnostic only.",
    }
