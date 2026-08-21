"""Compile normalized V3 evidence into protected, fail-closed candidates.

This is deliberately a staged evaluator. It does not materialize the nominal
billions of policy combinations. It evaluates a predeclared protection screen,
keeps one outcome per causal episode and policy, and leaves promotion blocked
until conservative execution and a sealed holdout exist.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from research_v3_contract import LADDERS, canonical_hash
from research_v3_policy_replay import replay_protected_policy
from research_v3_validation import validate_policy


def protection_screen() -> list[dict[str, Any]]:
    """Small, auditable Stage-1 safety screen requested by the user."""
    rows: list[dict[str, Any]] = []

    def add(name: str, *, atr_sl=None, thesis=None, thesis_sec=0, hard=30,
            time_stop=None, ladder="none", be_arm=None, be_floor=0,
            giveback_abs=None, giveback_fraction=None) -> None:
        rows.append({
            "protection_id": name,
            "loss_protection": {
                "atr_stop_k": atr_sl,
                "thesis_cut_margin_pct": thesis,
                "thesis_window_sec": thesis_sec,
                "hard_stop_margin_pct": hard,
                "time_stop_min": time_stop,
            },
            "profit_protection": {
                "atr_tp_k": 2.5,
                "ladder": [list(rung) for rung in LADDERS[ladder]],
                "break_even_arm_mfe_pct": be_arm,
                "break_even_floor_pct": be_floor,
                "mfe_giveback_abs_pct": giveback_abs,
                "mfe_giveback_fraction": giveback_fraction,
            },
        })

    for stop in (1.0, 1.5, 2.0):
        add(f"ATR_TP_2.5_ATR_SL_{stop:g}", atr_sl=stop)
    add("ATR_TP_2.5_THESIS_12_HARD_30", thesis=-12, thesis_sec=300)
    add("ATR_TP_2.5_SCENARIO_C", thesis=-12, thesis_sec=300, ladder="scenario_c")
    for minutes in (30, 60, 90, 120):
        add(f"ATR_TP_2.5_TIME_{minutes}", time_stop=minutes)
    for arm in (2, 4, 6):
        for floor in (0, 1):
            add(f"ATR_TP_2.5_BE_{arm}_LOCK_{floor}", be_arm=arm, be_floor=floor)
    for giveback in (2, 4, 8):
        add(f"ATR_TP_2.5_GIVEBACK_{giveback}", giveback_abs=giveback)
    for fraction in (0.2, 0.4, 0.6):
        add(f"ATR_TP_2.5_GIVEBACK_{int(fraction * 100)}PCT", giveback_fraction=fraction)
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
    result = []
    for event_id in sorted(set(intents) & set(terminal)):
        intent, lifecycle = intents[event_id], terminal[event_id]
        episode_id = str(lifecycle.get("episode_id") or intent.get("episode_id") or "")
        one_second_rows = []
        for ref in lifecycle.get("market_segment_refs") or []:
            if str(ref.get("timeframe")) == "1s":
                one_second_rows.extend(_load_segment(root, ref))
        feature = (opportunities.get(episode_id) or {}).get("feature_snapshot_at_signal") or {}
        result.append({
            "event_id": event_id,
            "episode_id": episode_id,
            "signal_ts": (opportunities.get(episode_id) or {}).get("signal_ts"),
            "regime": feature.get("regime") or feature.get("market_regime") or "UNKNOWN",
            "direction": intent.get("executed_direction"),
            "atr14_pct": intent.get("atr14_pct"),
            "leverage": intent.get("leverage") or 100.0,
            "margin_usd": intent.get("margin_usd") or 20.0,
            "entry_children": intent.get("entry_children") or [],
            "ordered_1s_prices": one_second_rows,
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


def evaluate_protection_screen(inputs: list[dict[str, Any]], *, sealed_holdout: bool = False) -> dict[str, Any]:
    """Evaluate exact policies; return descriptive rows and gated candidates."""
    episodes_by_policy: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    policy_specs: dict[str, dict[str, Any]] = {}
    protections = protection_screen()
    for source in inputs:
        episode_id = str(source.get("episode_id") or "")
        if not episode_id:
            continue
        prices = _ordered_prices(source.get("ordered_1s_prices") or [])
        for child in source.get("entry_children") or []:
            entry_id = str(child.get("entry_policy_id") or "")
            if not entry_id:
                continue
            for protection in protections:
                policy_id = f"{entry_id}|{protection['protection_id']}"
                spec = {
                    "entry": {"entry_policy_id": entry_id, "offset_pct": child.get("offset_pct"), "chase_id": child.get("chase_id")},
                    "fill": {"execution_world": "IDEAL_TOUCH_DIAGNOSTIC", "source_fill_model": child.get("fill_model")},
                    "loss_protection": protection["loss_protection"],
                    "profit_protection": protection["profit_protection"],
                    "portfolio": {"concurrency_cap": 1, "size_scale": 1.0, "daily_loss_kill_pct": 3},
                }
                policy_specs[policy_id] = spec
                outcome: dict[str, Any]
                if child.get("fill_ts") is None:
                    outcome = {"outcome_state": "NO_FILL", "net_pnl_usd": None}
                elif not prices or source.get("atr14_pct") is None:
                    outcome = {"outcome_state": "UNSUPPORTED", "reason": "ORDERED_1S_PATH_OR_ATR_MISSING"}
                else:
                    replay = replay_protected_policy(
                        prices,
                        direction=str(source.get("direction") or "UNKNOWN"),
                        entry_price=float(child.get("fill_price") or 0),
                        fill_ts=float(child["fill_ts"]),
                        atr_pct_at_fill=float(source["atr14_pct"]),
                        leverage=float(source.get("leverage") or 100),
                        margin_usd=float(source.get("margin_usd") or 20),
                        policy_spec=spec,
                    )
                    outcome = {
                        "outcome_state": "FULL_FILL" if replay.get("status") == "COMPLETE" else str(replay.get("status") or "UNSUPPORTED"),
                        "net_pnl_usd": replay.get("net_pnl_usd"),
                        "exit_reason": replay.get("exit_reason"),
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
                        "policy_outcomes": {policy_id: outcome},
                    }

    assessed = []
    policies_tested = max(1, len(episodes_by_policy))
    for policy_id, by_episode in episodes_by_policy.items():
        rows = sorted(by_episode.values(), key=lambda row: float(row.get("signal_ts") or 0))
        holdout_start = int(len(rows) * 0.7)
        oos = rows[holdout_start:]
        validation = validate_policy(
            oos,
            policy_id=policy_id,
            starting_equity_usd=1000,
            max_drawdown_usd=50,
            max_drawdown_pct=5,
            min_cvar95_usd=-10,
            policies_tested=policies_tested,
            conservative_execution=False,
            neighborhood_stable=False,
            sealed_holdout=sealed_holdout,
            liquidation_buffer_verified=False,
        )
        risk = validation["risk"]
        assessed.append({
            "policy_id": policy_id,
            "policy_signature": canonical_hash("v3-policy", policy_specs[policy_id]),
            "policy_spec": policy_specs[policy_id],
            "episodes_total": len(rows),
            "oos_episodes": len(oos),
            "sealed_oos_net_usd": risk.get("net_pnl_usd"),
            "max_drawdown_usd": risk.get("max_drawdown_usd"),
            "cvar95_usd": risk.get("cvar95_usd"),
            "expectancy_lcb_usd": validation["bootstrap"].get("mean_lcb95"),
            "gates": validation["gates"],
            "validation": validation,
        })
    descriptive = sorted(assessed, key=lambda row: (
        -float(row.get("sealed_oos_net_usd") or 0),
        abs(float(row.get("max_drawdown_usd") or 0)),
        str(row["policy_id"]),
    ))[:100]
    return {
        "schema": "safe_policy_candidate_screen_v3",
        "stage": "STAGE_1_PROTECTION_SCREEN",
        "input_events": len(inputs),
        "unique_policies_evaluated": len(assessed),
        "protection_variants": len(protections),
        "candidates": assessed,
        "descriptive_top_100": descriptive,
        "warning": "Descriptive rows use ideal-touch entry receipts and cannot qualify conservative execution or authorize live trading.",
    }
