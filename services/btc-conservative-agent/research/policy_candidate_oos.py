"""Chronological, episode-deduped static-versus-dynamic policy research.

This producer is deliberately fail-closed. It emits descriptive challengers
from complete stored paths, but cannot qualify ideal-touch evidence for live
activation. Qualification still requires conservative/actual execution,
costs, adequate independent OOS episodes, stability, and drawdown gates.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from collector_v22_schema import RESEARCH_EVENTS_FILE
from replay_eligibility import validate_replay_eligibility
from replay_event_report import replay_event_report
from research.best_policy_research import (
    POLICY_CANDIDATE_OOS_REPORT_FILE,
    QUALIFICATION_GATE_SCHEMA,
    REQUIRED_QUALIFICATION_GATES,
)
from policy_search_manifest import POLICY_SEARCH_MANIFEST

MIN_TRAIN_EPISODES = 30
MIN_OOS_EPISODES = 20
MIN_PROMOTION_EPISODES = 100
MAX_DESCRIPTIVE_STATIC_ROWS = 100


def _oos_policy_comparison(static_oos, dynamic_oos) -> dict:
    """Separate absolute profitability from merely being less unprofitable."""
    static = static_oos or {}
    dynamic = dynamic_oos or {}
    static_n = int(static.get("independent_episodes") or 0)
    dynamic_n = int(dynamic.get("independent_episodes") or 0)
    static_ev = static.get("expectancy_usd")
    dynamic_ev = dynamic.get("expectancy_usd")
    static_net = static.get("net_pnl_usd")
    dynamic_net = dynamic.get("net_pnl_usd")

    def profitable(n, ev, net):
        return bool(n > 0 and ev is not None and net is not None and float(ev) > 0 and float(net) > 0)

    profitable_kinds = []
    if profitable(static_n, static_ev, static_net):
        profitable_kinds.append(("STATIC", float(static_ev), float(static_net)))
    if profitable(dynamic_n, dynamic_ev, dynamic_net):
        profitable_kinds.append(("DYNAMIC", float(dynamic_ev), float(dynamic_net)))
    if len(profitable_kinds) == 2 and profitable_kinds[0][1:] == profitable_kinds[1][1:]:
        winner = "TIE"
    else:
        winner = max(profitable_kinds, key=lambda row: (row[1], row[2]))[0] if profitable_kinds else "NONE"

    relative = "NONE"
    if static_n > 0 and dynamic_n > 0 and static_ev is not None and dynamic_ev is not None:
        if float(dynamic_ev) > float(static_ev):
            relative = "DYNAMIC"
        elif float(static_ev) > float(dynamic_ev):
            relative = "STATIC"
        else:
            relative = "TIE"
    return {
        "winner_kind": winner,
        "winner_status": "PROFITABLE_OOS_WINNER" if winner != "NONE" else "NO_PROFITABLE_OOS_WINNER",
        "relative_leader_kind": relative,
        "comparison_delta": {
            "dynamic_minus_static_expectancy_usd": None if static_ev is None or dynamic_ev is None else round(float(dynamic_ev) - float(static_ev), 6),
            "dynamic_minus_static_net_pnl_usd": None if static_net is None or dynamic_net is None else round(float(dynamic_net) - float(static_net), 4),
            "dynamic_minus_static_completed_episodes": dynamic_n - static_n,
        },
    }


def _read_events(path: Path) -> list[dict]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        pass
    return rows


def _signal_ts(row: dict) -> float:
    try:
        return float((row.get("envelope") or {}).get("signal_ts") or row.get("signal_ts") or 0)
    except (TypeError, ValueError):
        return 0.0


def _regime_key(event: dict) -> str:
    snap = event.get("feature_snapshot_at_signal") or {}
    cycle = snap.get("cycle_3m_universe") or {}
    source = snap.get("source_features") or {}
    context = snap.get("market_context") or source.get("market_context") or {}
    regime = str(cycle.get("regime") or context.get("regime_label") or source.get("regime") or "UNKNOWN").upper()
    try:
        adx = float(cycle.get("adx14") or 0)
    except (TypeError, ValueError):
        adx = 0.0
    adx_bucket = "ADX_LT20" if adx < 20 else "ADX_20_25" if adx < 25 else "ADX_25_30" if adx < 30 else "ADX_GE30"
    try:
        atr = float(cycle.get("atr14_pct_3m") or 0)
    except (TypeError, ValueError):
        atr = 0.0
    atr_bucket = "ATR_LOW" if atr < 0.08 else "ATR_MID" if atr < 0.16 else "ATR_HIGH"
    session = str(cycle.get("session_utc") or source.get("session_bucket") or "UNKNOWN").upper()
    return f"{regime}|{adx_bucket}|{atr_bucket}|{session}"


def _episode_rows(events: list[dict]) -> list[dict]:
    earliest = {}
    for row in sorted(events, key=_signal_ts):
        episode = str(row.get("event_episode_id") or (row.get("envelope") or {}).get("event_episode_id") or "")
        if episode and episode not in earliest:
            earliest[episode] = row
    return list(earliest.values())


def _primary_outcome(event: dict) -> str:
    return str(
        event.get("primary_outcome")
        or (event.get("envelope") or {}).get("primary_outcome")
        or ""
    )


def _policy_outcomes(event: dict) -> dict[str, float]:
    report = replay_event_report(event)
    if report.get("replay_status") != "REPLAY_ELIGIBLE":
        return {}
    outcomes = {}
    control = report.get("control_outcome") or {}
    if control.get("pnl") is not None:
        outcomes["CONTROL"] = float(control["pnl"])
    for entry in report.get("hypothetical_entries") or []:
        entry_id = str(entry.get("entry_policy_id") or "")
        if not entry_id:
            continue
        for score in entry.get("stage1_scores") or []:
            exit_id = str(score.get("exit") or "")
            pnl = score.get("pnl")
            if exit_id and pnl is not None:
                outcomes[f"{entry_id}|{exit_id}"] = float(pnl)
    return outcomes


def _evaluate(policy_id: str, rows: list[dict], cache: dict[str, dict[str, float]]) -> dict:
    values = [cache[row["event_id"]].get(policy_id, 0.0) for row in rows]
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        running += value
        peak = max(peak, running)
        max_drawdown = min(max_drawdown, running - peak)
    return {
        "policy_id": policy_id,
        "independent_episodes": len(values),
        "fills": sum(1 for value in values if value != 0),
        "wins": sum(1 for value in values if value > 0),
        "losses": sum(1 for value in values if value < 0),
        "net_pnl_usd": round(sum(values), 4),
        "expectancy_usd": None if not values else round(sum(values) / len(values), 6),
        "max_drawdown_usd": round(max_drawdown, 4),
    }


def build_policy_candidate_oos_report(data_dir=".", report_dir=".") -> dict:
    events = _read_events(Path(data_dir) / RESEARCH_EVENTS_FILE)
    latest = max(events, key=_signal_ts, default={})
    epoch = str(latest.get("epoch_id") or (latest.get("envelope") or {}).get("epoch_id") or "")
    policy_epoch = str(latest.get("policy_epoch_id") or (latest.get("envelope") or {}).get("policy_epoch_id") or "")
    signature = str(latest.get("policy_signature") or (latest.get("envelope") or {}).get("policy_signature") or "")
    current = [
        row for row in events
        if str(row.get("epoch_id") or (row.get("envelope") or {}).get("epoch_id") or "") == epoch
        and str(
            row.get("policy_epoch_id")
            or (row.get("envelope") or {}).get("policy_epoch_id")
            or ""
        ) == policy_epoch
    ]
    eligible = [row for row in current if validate_replay_eligibility(row).get("eligible")]
    episodes = _episode_rows(eligible)
    cache = {str(row.get("event_id")): _policy_outcomes(row) for row in episodes}
    for row in episodes:
        row["event_id"] = str(row.get("event_id"))
    split = max(1, int(len(episodes) * 0.7)) if episodes else 0
    train, oos = episodes[:split], episodes[split:]
    policies = sorted({policy for values in cache.values() for policy in values})
    train_rank = sorted((_evaluate(policy, train, cache) for policy in policies), key=lambda row: (row["expectancy_usd"] or -1e9, row["net_pnl_usd"]), reverse=True)
    static_id = train_rank[0]["policy_id"] if train_rank else None
    static_oos = _evaluate(static_id, oos, cache) if static_id else None
    static_train = _evaluate(static_id, train, cache) if static_id else None
    # Rank on the training partition only, then expose the untouched OOS result.
    # Filtering or sorting directly on OOS would turn the dashboard into a
    # multiple-testing winner picker and make attractive noise look causal.
    profitable_static = []
    for train_result in train_rank:
        if (train_result.get("expectancy_usd") or 0) <= 0:
            continue
        oos_result = _evaluate(train_result["policy_id"], oos, cache)
        if (oos_result.get("expectancy_usd") or 0) <= 0:
            continue
        profitable_static.append({
            "policy_id": train_result["policy_id"],
            "train": train_result,
            "oos": oos_result,
            "qualification": "DESCRIPTIVE_ONLY",
        })
        if len(profitable_static) >= MAX_DESCRIPTIVE_STATIC_ROWS:
            break

    regime_train = defaultdict(list)
    for row in train:
        regime_train[_regime_key(row)].append(row)
    regime_map = {}
    for regime, rows in regime_train.items():
        ranked = sorted((_evaluate(policy, rows, cache) for policy in policies), key=lambda item: (item["expectancy_usd"] or -1e9), reverse=True)
        if ranked:
            regime_map[regime] = ranked[0]["policy_id"]
    dynamic_values = []
    per_regime_oos = defaultdict(list)
    for row in oos:
        regime = _regime_key(row)
        policy = regime_map.get(regime, "CONTROL")
        value = cache[row["event_id"]].get(policy, 0.0)
        dynamic_values.append(value)
        per_regime_oos[regime].append(value)
    dynamic_oos = {
        "independent_episodes": len(dynamic_values),
        "net_pnl_usd": round(sum(dynamic_values), 4),
        "expectancy_usd": None if not dynamic_values else round(sum(dynamic_values) / len(dynamic_values), 6),
    }
    dynamic_regimes = []
    for regime, train_rows in sorted(regime_train.items()):
        chosen = regime_map.get(regime, "CONTROL")
        oos_rows = [row for row in oos if _regime_key(row) == regime]
        train_ready = len(train_rows) >= MIN_TRAIN_EPISODES
        oos_ready = len(oos_rows) >= MIN_OOS_EPISODES
        fallback = not (train_ready and oos_ready)
        dynamic_regimes.append({
            "regime": regime,
            "selected_policy_id": chosen if not fallback else "CONTROL_OR_NO_TRADE",
            "research_candidate_policy_id": chosen,
            "train": _evaluate(chosen, train_rows, cache),
            "oos": _evaluate(chosen, oos_rows, cache),
            "fallback": fallback,
            "fallback_reason": None if not fallback else (
                "INSUFFICIENT_REGIME_TRAIN_EPISODES" if not train_ready
                else "INSUFFICIENT_REGIME_OOS_EPISODES"
            ),
            "required_train_episodes": MIN_TRAIN_EPISODES,
            "required_oos_episodes": MIN_OOS_EPISODES,
            "qualification": "DESCRIPTIVE_ONLY",
        })
    unknown_oos = [row for row in oos if _regime_key(row) not in regime_map]
    if unknown_oos:
        dynamic_regimes.append({
            "regime": "UNKNOWN_OR_UNSEEN",
            "selected_policy_id": "CONTROL_OR_NO_TRADE",
            "research_candidate_policy_id": None,
            "train": None,
            "oos": _evaluate("CONTROL", unknown_oos, cache),
            "fallback": True,
            "fallback_reason": "REGIME_UNSEEN_IN_TRAINING",
            "required_train_episodes": MIN_TRAIN_EPISODES,
            "required_oos_episodes": MIN_OOS_EPISODES,
            "qualification": "DESCRIPTIVE_ONLY",
        })

    # Rejected opportunities form the clean shadow cohort in v2.2: no real
    # order was allowed, but the same immutable path is available for replay.
    shadow_rows = _episode_rows([
        row for row in eligible if _primary_outcome(row) == "REJECTED"
    ])
    shadow_cache = {
        str(row.get("event_id")): _policy_outcomes(row) for row in shadow_rows
    }
    for row in shadow_rows:
        row["event_id"] = str(row.get("event_id"))
    shadow_policies = sorted({
        policy for values in shadow_cache.values() for policy in values
    })
    shadow_rank = sorted(
        (_evaluate(policy, shadow_rows, shadow_cache) for policy in shadow_policies),
        key=lambda row: (
            row["expectancy_usd"] or -1e9,
            row["net_pnl_usd"],
        ),
        reverse=True,
    )
    profitable_shadow = [
        {**row, "qualification": "SHADOW_DESCRIPTIVE_ONLY"}
        for row in shadow_rank
        if (row.get("expectancy_usd") or 0) > 0
    ][:MAX_DESCRIPTIVE_STATIC_ROWS]

    enough = len(train) >= MIN_TRAIN_EPISODES and len(oos) >= MIN_OOS_EPISODES
    gates = {name: False for name in REQUIRED_QUALIFICATION_GATES}
    gates.update({
        "chronological_untouched_oos": enough,
        "minimum_independent_episodes": len(episodes) >= MIN_PROMOTION_EPISODES,
        "regime_diversity": len(regime_train) >= 3,
        "no_data_integrity_defects": len(eligible) == len(current) and bool(current),
        "control_benchmark_comparison": bool(static_oos and static_oos.get("expectancy_usd") is not None),
    })
    blockers = [f"QUALIFICATION_GATE_FAILED:{name}" for name, passed in gates.items() if not passed]
    candidate = None
    descriptive = None
    if static_oos:
        comparison = _oos_policy_comparison(static_oos, dynamic_oos)
        descriptive = {
            **comparison,
            "static_train": static_train,
            "static_oos": static_oos,
            "dynamic_oos": dynamic_oos,
            "regime_policy_map": regime_map,
            "profitable_static_policies": profitable_static,
            "dynamic_regimes": dynamic_regimes,
            "multiple_testing_warning": (
                "Policies are ranked on training only and shown with later OOS. "
                "They remain descriptive until stability, conservative execution, "
                "cost and minimum-sample gates pass."
            ),
            "note": "Descriptive only; ideal-touch paths and small OOS cannot authorize trading.",
        }
    report = {
        "schema": "policy_candidate_oos_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epoch_id": epoch or None,
        "policy_epoch_id": policy_epoch or None,
        "evidence_policy_signature": signature or None,
        "search_manifest_signature": POLICY_SEARCH_MANIFEST["signature"],
        "status": "QUALIFIED" if candidate else "BLOCKED",
        "independent_oos_qualified": False,
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gates": gates,
        "candidate": candidate,
        "descriptive_challenger": descriptive,
        "evidence": {
            "current_events": len(current), "eligible_events": len(eligible),
            "independent_episodes": len(episodes), "training_episodes": len(train),
            "oos_episodes": len(oos), "policies_observed": len(policies),
            "shadow_independent_episodes": len(shadow_rows),
        },
        "shadow_research": {
            "scope": "REJECTED_CURRENT_EPOCH_REPLAY",
            "independent_episodes": len(shadow_rows),
            "profitable_policies": profitable_shadow,
            "qualification": "DESCRIPTIVE_ONLY",
            "note": (
                "Rejected opportunities are replayed as shadow evidence and "
                "never merged with actual executed PnL."
            ),
        },
        "blockers": sorted(set(blockers)),
    }
    root = Path(report_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / POLICY_CANDIDATE_OOS_REPORT_FILE
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temp.replace(target)
    return report
