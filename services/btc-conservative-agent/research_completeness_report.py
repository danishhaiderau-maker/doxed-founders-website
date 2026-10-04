"""Analyzer view of research-completeness evidence.

Reads the lifecycle completeness fields, shadow identity/cost/depth context,
the hard-vs-ATR stop axis, AI decision usefulness and frozen trial stamps.
Rows written before these fields existed are reported as missing
(``LEGACY_ROW_FIELD_ABSENT``); they never crash the report and are never
counted as complete.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from analyzer_epoch_guard import epoch_lines

REPORT_SCHEMA = "research_completeness_report_v1"
LIFECYCLE_FIELDS = (
    "session_label", "mae_ts", "mfe_ts", "fill_revalidation_count",
    "no_fill_ttl_outcome", "exit_depth",
)
# A null value is acceptable only when paired with an explicit reason.
_FIELD_REASON = {
    "session_label": "session_label_basis",
    "mae_ts": "mae_ts_basis",
    "mfe_ts": "mfe_ts_basis",
    "fill_revalidation_count": "fill_revalidation_count_reason",
    "no_fill_ttl_outcome": "no_fill_ttl_outcome_reason",
    "exit_depth": "exit_depth_unavailable_reason",
}
SHADOW_FILES = ("shadow_outcome.jsonl", "shadow_lane_outcome.jsonl")
MAX_SHADOW_ROWS_PER_FILE = 200_000


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def load_shadow_rows(data_dir: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Tolerantly read shadow outcome JSONL files; malformed lines are counted."""
    rows: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {"files": {}, "parse_errors": 0, "truncated": False}
    for name in SHADOW_FILES:
        path = Path(data_dir) / name
        count = 0
        if path.is_file():
            for line in epoch_lines(path, "r", encoding="utf-8-sig", errors="replace"):
                if count >= MAX_SHADOW_ROWS_PER_FILE:
                    receipt["truncated"] = True
                    break
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    receipt["parse_errors"] += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                    count += 1
        receipt["files"][name] = count
    return rows, receipt


def _lifecycle_kind(row: Mapping[str, Any]) -> str | None:
    if str(row.get("observation_status") or "").upper() == "PAPER_POSITION_CLOSED":
        return "CLOSED"
    if row.get("terminal_no_fill") is True or str(row.get("outcome_state") or "").upper() == "NO_FILL":
        return "NO_FILL"
    return None


def lifecycle_completeness_coverage(lifecycles: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-field present / explained-null / missing counts for terminal rows."""
    out: dict[str, Any] = {}
    for kind in ("CLOSED", "NO_FILL"):
        out[kind] = {
            "rows": 0, "legacy_rows": 0,
            "fields": {field: {"present": 0, "null_with_reason": 0, "missing": 0}
                       for field in LIFECYCLE_FIELDS},
            "no_fill_ttl_outcomes": {}, "session_labels": {},
        }
    for row in lifecycles or ():
        if not isinstance(row, Mapping):
            continue
        kind = _lifecycle_kind(row)
        if kind is None:
            continue
        bucket = out[kind]
        bucket["rows"] += 1
        if not row.get("lifecycle_completeness_schema"):
            bucket["legacy_rows"] += 1
        for field in LIFECYCLE_FIELDS:
            stats = bucket["fields"][field]
            if row.get(field) is not None:
                stats["present"] += 1
            elif field in row and row.get(_FIELD_REASON[field]):
                stats["null_with_reason"] += 1
            else:
                stats["missing"] += 1
        outcome = row.get("no_fill_ttl_outcome")
        if outcome:
            counts = bucket["no_fill_ttl_outcomes"]
            counts[str(outcome)] = counts.get(str(outcome), 0) + 1
        label = str(row.get("session_label") or (
            "LEGACY_ROW_FIELD_ABSENT" if "session_label" not in row else "UNKNOWN"
        ))
        bucket["session_labels"][label] = bucket["session_labels"].get(label, 0) + 1
    return out


def shadow_rank_gate(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Split shadow rows into rank-eligible and excluded (with reasons)."""
    eligible: list[Mapping[str, Any]] = []
    reasons: Counter[str] = Counter()
    excluded = 0
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        row_reasons = []
        if not row.get("shadow_completeness_schema"):
            row_reasons.append("LEGACY_ROW_WITHOUT_COMPLETENESS")
        else:
            identity = row.get("shadow_policy_identity")
            identity = identity if isinstance(identity, Mapping) else {}
            if row.get("shadow_identity_status") != "COMPLETE" or not all(
                identity.get(key) for key in ("tile_lane", "policy_signature", "collection_epoch_id")
            ):
                row_reasons.append("IDENTITY_MISSING")
            if (row.get("cost_assumptions_status") != "COMPLETE"
                    or not isinstance(row.get("cost_assumptions"), Mapping)):
                row_reasons.append("COSTS_MISSING")
            if row.get("shadow_depth_status") != "TOP_OF_BOOK_QTY":
                row_reasons.append("DEPTH_MISSING")
        if row_reasons:
            excluded += 1
            reasons.update(row_reasons)
        else:
            eligible.append(row)
    return {"eligible": eligible, "eligible_count": len(eligible), "excluded_count": excluded,
            "exclusion_reasons": dict(sorted(reasons.items()))}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def shadow_leaderboard(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Rank shadow lanes by mean net PnL using only rank-gate eligible rows."""
    gate = shadow_rank_gate(rows)
    by_lane: dict[str, list[float]] = defaultdict(list)
    no_fill: Counter[str] = Counter()
    for row in gate["eligible"]:
        lane = str((row.get("shadow_policy_identity") or {}).get("tile_lane") or "UNKNOWN")
        pnl = _finite(row.get("net_pnl_usd"))
        if row.get("filled") is True and pnl is not None:
            by_lane[lane].append(pnl)
        elif row.get("filled") is not True:
            no_fill[lane] += 1
    leaderboard = sorted((
        {"tile_lane": lane, "filled_rows": len(values), "no_fill_rows": no_fill.get(lane, 0),
         "mean_net_pnl_usd": _mean(values), "total_net_pnl_usd": round(sum(values), 6)}
        for lane, values in by_lane.items()
    ), key=lambda item: item["mean_net_pnl_usd"], reverse=True)
    return {
        "rank_gate": {key: gate[key] for key in ("eligible_count", "excluded_count", "exclusion_reasons")},
        "leaderboard": leaderboard,
    }


def stop_axis_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate the shadow-only hard-stop vs ATR-stop counterfactual."""
    status: Counter[str] = Counter()
    first: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    returns: dict[str, list[float]] = {"hard_stop": [], "atr_stop": []}
    for row in rows or ():
        if not isinstance(row, Mapping) or str(row.get("observation_status") or "") != "PAPER_POSITION_CLOSED":
            continue
        axis = row.get("stop_axis_counterfactual")
        if not isinstance(axis, Mapping):
            status["LEGACY_ROW_FIELD_ABSENT"] += 1
            continue
        status[str(axis.get("status") or "UNKNOWN")] += 1
        if axis.get("first_trigger"):
            first[str(axis["first_trigger"])] += 1
        for arm in ("hard_stop", "atr_stop"):
            detail = axis.get(arm) if isinstance(axis.get(arm), Mapping) else {}
            if detail.get("status") == "HIT":
                hits[arm] += 1
            value = _finite(detail.get("margin_return_pct"))
            if value is not None:
                returns[arm].append(value)
    return {
        "shadow_only": True,
        "status_counts": dict(sorted(status.items())),
        "first_trigger_counts": dict(sorted(first.items())),
        "hit_counts": dict(sorted(hits.items())),
        "mean_margin_return_pct": {arm: (round(sum(v) / len(v), 4) if v else None) for arm, v in returns.items()},
        "samples": {arm: len(values) for arm, values in returns.items()},
    }


def _side(decision: Mapping[str, Any]) -> str:
    long_score, short_score = _finite(decision.get("long_score")), _finite(decision.get("short_score"))
    if long_score is None or short_score is None:
        gap = _finite(decision.get("score_gap"))
        if gap is None:
            return "UNKNOWN"
        return "TIE" if gap == 0 else "SIDED"
    return "TIE" if long_score == short_score else "SIDED"


def _approval(decision: Mapping[str, Any]) -> str:
    raw = str(decision.get("raw_ai_decision") or "").upper()
    if raw in {"APPROVE", "APPROVED", "STRONG_APPROVE", "SOFT_APPROVE"}:
        return "APPROVED"
    if raw in {"REJECT", "REJECTED", "SOFT_REJECT", "AI_REJECT", "AI_REJECTED",
               "NO_TRADE", "CONFLICTED", "BELOW_THRESHOLD"}:
        return "REJECTED"
    return "UNKNOWN"


def ai_usefulness_report(
    decisions: Iterable[Mapping[str, Any]], lifecycles: Iterable[Mapping[str, Any]],
    shadow_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Tie vs sided counts and approved vs rejected decision outcomes."""
    lane_decisions = [
        row for row in decisions or ()
        if isinstance(row, Mapping) and str(row.get("decision_stage") or "") == "LANE_POLICY_VERDICT"
    ]
    per_call: dict[str, Mapping[str, Any]] = {}
    for row in lane_decisions:
        key = str(row.get("shared_ai_call_id") or row.get("episode_id") or "")
        if key:
            per_call.setdefault(key, row)
    closed = {
        (str(row.get("episode_id") or ""), str(row.get("research_lane") or "").upper()): row
        for row in lifecycles or ()
        if isinstance(row, Mapping) and str(row.get("observation_status") or "") == "PAPER_POSITION_CLOSED"
    }
    shadow_by_call: dict[str, list[float]] = defaultdict(list)
    for row in shadow_rank_gate(shadow_rows)["eligible"]:
        pnl = _finite(row.get("net_pnl_usd"))
        call = str(row.get("shared_ai_call_id") or "")
        if call and row.get("filled") is True and pnl is not None:
            shadow_by_call[call].append(pnl)
    groups = {label: {"decisions": 0, "paper_net_pnl": [], "shadow_net_pnl": []}
              for label in ("APPROVED", "REJECTED", "UNKNOWN")}
    side_by_approval: dict[str, Counter[str]] = defaultdict(Counter)
    for row in lane_decisions:
        approval = _approval(row)
        groups[approval]["decisions"] += 1
        side_by_approval[approval][_side(row)] += 1
        match = closed.get((str(row.get("episode_id") or ""), str(row.get("research_lane") or "").upper()))
        pnl = _finite((match or {}).get("net_pnl_usd"))
        if pnl is not None:
            groups[approval]["paper_net_pnl"].append(pnl)
    for call, row in per_call.items():
        groups[_approval(row)]["shadow_net_pnl"].extend(shadow_by_call.get(call) or [])
    return {
        "ai_calls": len(per_call),
        "lane_decisions": len(lane_decisions),
        "tie_vs_sided_calls": dict(sorted(Counter(_side(row) for row in per_call.values()).items())),
        "tie_vs_sided_by_approval": {k: dict(sorted(v.items())) for k, v in sorted(side_by_approval.items())},
        "approved_vs_rejected": {
            label: {
                "decisions": group["decisions"],
                "paper_closed_outcomes": len(group["paper_net_pnl"]),
                "paper_mean_net_pnl_usd": _mean(group["paper_net_pnl"]),
                "paper_win_rate": (
                    round(sum(1 for v in group["paper_net_pnl"] if v > 0) / len(group["paper_net_pnl"]), 4)
                    if group["paper_net_pnl"] else None
                ),
                "shadow_rank_eligible_filled": len(group["shadow_net_pnl"]),
                "shadow_mean_net_pnl_usd": _mean(group["shadow_net_pnl"]),
            }
            for label, group in groups.items()
        },
        "outcome_basis": "PAPER_CLOSED_LIFECYCLES_PLUS_RANK_GATED_SHADOWS",
    }


def frozen_trial_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Candidate vs control results for rows stamped by a frozen trial."""
    trials: dict[str, dict[str, Any]] = {}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        stamp = row.get("frozen_trial")
        if not isinstance(stamp, Mapping) or stamp.get("paper_only") is not True:
            continue
        trial = trials.setdefault(str(stamp.get("trial_id")), {
            "trial_signature": stamp.get("trial_signature"),
            "arms": {arm: {"rows": 0, "net_pnl": []} for arm in ("CANDIDATE", "CONTROL")},
        })
        arm = trial["arms"].get(str(stamp.get("arm")))
        if arm is None:
            continue
        arm["rows"] += 1
        pnl = _finite(row.get("net_pnl_usd"))
        if str(row.get("observation_status") or "") == "PAPER_POSITION_CLOSED" and pnl is not None:
            arm["net_pnl"].append(pnl)
    return {
        trial_id: {
            "trial_signature": trial["trial_signature"],
            "arms": {arm: {"rows": data["rows"], "closed": len(data["net_pnl"]),
                           "mean_net_pnl_usd": _mean(data["net_pnl"])}
                     for arm, data in trial["arms"].items()},
        }
        for trial_id, trial in trials.items()
    }


def build_research_completeness_report(
    data_dir: str | Path, *, lifecycles: Iterable[Mapping[str, Any]] = (),
    decisions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    lifecycles = [row for row in lifecycles or () if isinstance(row, Mapping)]
    decisions = [row for row in decisions or () if isinstance(row, Mapping)]
    shadow_rows, shadow_receipt = load_shadow_rows(data_dir)
    return {
        "schema": REPORT_SCHEMA,
        "lifecycle_completeness": lifecycle_completeness_coverage(lifecycles),
        "shadow_ranking": shadow_leaderboard(shadow_rows),
        "shadow_source": shadow_receipt,
        "stop_axis_counterfactual": stop_axis_summary(lifecycles),
        "ai_usefulness": ai_usefulness_report(decisions, lifecycles, shadow_rows),
        "frozen_trials": frozen_trial_report(lifecycles),
        "live_policy_effect": "NONE",
    }
