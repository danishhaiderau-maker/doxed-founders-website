"""Minimum-B replay report for one v2.2 event_id."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Mapping, Optional, Sequence

from collector_v22_schema import (
    COLLECTOR_VERSION,
    OBS_INSUFFICIENT_PATH,
    OBS_UNSUPPORTED_HORIZON,
    OBS_WAITING_120M,
    PATH_WINDOW_POLICY_ID,
    POLICY_ID,
)
from collector_storage import project_capacity
from collector_v22_schema import RESEARCH_EVENTS_FILE
from collector_v22 import BYTES_PER_EVENT_TYPICAL, _load_event_index
from path_replay_v1 import (
    CONTROL_CELL,
    LIVE_THESIS_CUT,
    stage1_replay,
    raw_1m_to_ticks,
    path_recovery_stats,
)
from replay_eligibility import validate_replay_eligibility


AMBIGUOUS_SAME_BAR = "AMBIGUOUS_SAME_BAR"
ATR_FROZEN = "FROZEN_AT_ENTRY"
ATR_ROLLING = "ROLLING"


def _load_events(data_dir: str) -> list:
    path = os.path.join(data_dir, RESEARCH_EVENTS_FILE)
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


def find_event(data_dir: str, event_id: str) -> Optional[dict]:
    for row in _load_events(data_dir):
        if str(row.get("event_id") or row.get("trade_id")) == str(event_id):
            return row
    return None


def _control_replay(
    *,
    ticks: Sequence[Mapping[str, Any]],
    direction: str,
    entry_price: float,
    fill_t: float,
    atr14_pct: Optional[float],
    invert_on: bool,
) -> dict:
    sweep = stage1_replay(
        ticks,
        direction=direction,
        entry_price=entry_price,
        fill_t=fill_t,
        invert_on=invert_on,
        atr14_pct=atr14_pct,
    )
    scores = sweep.get("chase_exit_scores") or []
    live = next(
        (s for s in scores if s.get("exit") in ("live_4_2_t12", "thesis_m12", "live_c_t12")),
        scores[0] if scores else None,
    )
    result = {
        "exit_policy": live.get("exit") if live else None,
        "first_hit": live.get("first_hit") if live else None,
        "pnl": live.get("pnl") if live else None,
        "mfe_pct": live.get("mfe_pct") if live else None,
        "mae_pct": live.get("mae_pct") if live else None,
        "green": live.get("green") if live else None,
        "atr_mode": ATR_FROZEN,
        "structure_mode": ATR_FROZEN,
        "same_bar_ambiguity": sweep.get("same_bar_ambiguity") or False,
    }
    # A profit-lock label with negative replay PnL is not self-explanatory.
    # Real venues can gap/slip through a stop, but this ideal 1m replay has no
    # authenticated execution evidence capable of proving that mechanism.
    # Fail closed instead of publishing a semantically impossible winner label.
    profit_lock_labels = {"PROFIT_LOCK_LADDER", "PROFIT_LOCK", "PROFIT_LOCK_TRAIL"}
    try:
        negative = result["pnl"] is not None and float(result["pnl"]) < 0.0
    except (TypeError, ValueError):
        negative = False
    if negative and (
        str(result.get("exit_policy") or "").upper() in profit_lock_labels
        or str(result.get("first_hit") or "").upper() in profit_lock_labels
    ):
        result.update({
            "exit_policy": "SEMANTICALLY_INVALID_TERMINAL",
            "first_hit": "SEMANTICALLY_INVALID_TERMINAL",
            "replay_status": "REPLAY_INELIGIBLE",
            "semantic_error": "NEGATIVE_PNL_WITH_PROFIT_LOCK_LABEL",
            "green": False,
        })
    return result


def _delta_vs_control(control: Optional[Mapping], alt: Optional[Mapping]) -> dict:
    if not control or not alt:
        return {
            "delta_vs_control_exit": None,
            "delta_vs_control_entry": None,
            "delta_vs_no_trade": None,
            "would_have_hit_control_exit_first": None,
            "note": "no CONTROL trade must not be represented as zero PnL",
        }
    c_pnl = control.get("pnl")
    a_pnl = alt.get("pnl")
    delta_exit = None
    if c_pnl is not None and a_pnl is not None:
        delta_exit = round(float(a_pnl) - float(c_pnl), 4)
    return {
        "delta_vs_control_exit": delta_exit,
        "delta_vs_control_entry": alt.get("entry_delta_vs_control"),
        "delta_vs_no_trade": None if a_pnl is None else float(a_pnl),
        "would_have_hit_control_exit_first": control.get("first_hit"),
    }


def replay_event_report(event: Mapping[str, Any], *, data_dir: Optional[str] = None) -> dict:
    obs = str(event.get("observation_status") or "")
    eligibility = validate_replay_eligibility(event)
    if not eligibility["eligible"]:
        return {
            "schema": "replay_event_report_v1",
            "collector_version": COLLECTOR_VERSION,
            "event_id": event.get("event_id"),
            "replay_status": "REPLAY_INELIGIBLE",
            "observation_status": obs,
            "eligibility": eligibility,
            "control_outcome": None,
            "hypothetical_entries": [],
            "note": "never partially score — eligibility is derived from timestamps, not lifecycle labels",
        }
    if obs in (OBS_WAITING_120M, OBS_INSUFFICIENT_PATH, OBS_UNSUPPORTED_HORIZON):
        return {
            "schema": "replay_event_report_v1",
            "collector_version": COLLECTOR_VERSION,
            "event_id": event.get("event_id"),
            "replay_status": obs,
            "eligibility": eligibility,
            "control_outcome": None,
            "hypothetical_entries": [],
            "note": "complete tape is present, but lifecycle or requested-horizon status prohibits scoring",
        }
    tape = event.get("canonical_tape") or {}
    path_1m = tape.get("path_1m") or []
    direction = str((event.get("envelope") or {}).get("direction") or event.get("direction") or "SHORT")
    live_fill_ts = event.get("live_fill_ts")
    live_fill_price = event.get("live_fill_price")
    atr14 = event.get("atr14_pct")
    invert_on = bool(event.get("invert_on"))
    control_outcome = None
    hyp_reports = []
    if live_fill_ts and live_fill_price and path_1m:
        ticks = raw_1m_to_ticks(
            path_1m,
            direction=direction,
            start_ts=float(live_fill_ts),
            end_ts=float(live_fill_ts) + 7200.0,
        )
        if ticks:
            control_outcome = _control_replay(
                ticks=ticks,
                direction=direction,
                entry_price=float(live_fill_price),
                fill_t=float(live_fill_ts),
                atr14_pct=atr14,
                invert_on=invert_on,
            )
            recovery = path_recovery_stats(
                ticks, direction=direction, entry_price=float(live_fill_price), fill_t=float(live_fill_ts),
            )
            control_outcome["recovery"] = recovery
    for child in event.get("entry_children") or []:
        fill_ts = child.get("fill_ts")
        if fill_ts is None:
            continue
        fill_px = child.get("fill_price")
        if fill_px is None:
            continue
        ticks = raw_1m_to_ticks(
            path_1m,
            direction=direction,
            start_ts=float(fill_ts),
            end_ts=float(fill_ts) + 7200.0,
        )
        if not ticks:
            hyp_reports.append({
                "entry_policy_id": child.get("entry_policy_id"),
                "replay_status": OBS_INSUFFICIENT_PATH,
            })
            continue
        alt = _control_replay(
            ticks=ticks,
            direction=direction,
            entry_price=float(fill_px),
            fill_t=float(fill_ts),
            atr14_pct=atr14,
            invert_on=invert_on,
        )
        alt["entry_policy_id"] = child.get("entry_policy_id")
        alt["fill_ts"] = fill_ts
        alt.update(_delta_vs_control(control_outcome, alt))
        if alt.get("same_bar_ambiguity"):
            alt["ambiguity_code"] = AMBIGUOUS_SAME_BAR
        hyp_reports.append(alt)
    capacity = project_capacity(
        data_dir=data_dir,
        bytes_per_event_typical=BYTES_PER_EVENT_TYPICAL,
        events_per_day=_events_per_day_estimate(data_dir),
    )
    return {
        "schema": "replay_event_report_v1",
        "collector_version": COLLECTOR_VERSION,
        "event_id": event.get("event_id"),
        "epoch_id": event.get("epoch_id"),
        "policy_id": event.get("base_policy_id") or event.get("policy_id") or POLICY_ID,
        "base_policy_id": event.get("base_policy_id") or event.get("policy_id") or POLICY_ID,
        "policy_signature": event.get("policy_signature") or (event.get("envelope") or {}).get("policy_signature"),
        "policy_epoch_id": event.get("policy_epoch_id") or (event.get("envelope") or {}).get("policy_epoch_id"),
        "policy_identity": event.get("policy_identity") or (event.get("envelope") or {}).get("policy_identity"),
        "path_window_policy_id": PATH_WINDOW_POLICY_ID,
        "primary_outcome": event.get("primary_outcome"),
        "observation_status": obs,
        "replay_status": "REPLAY_ELIGIBLE",
        "eligibility": eligibility,
        "signal_ts": (event.get("envelope") or {}).get("signal_ts"),
        "decision_tree": event.get("decision_tree_snapshot"),
        "control_outcome": control_outcome,
        "control_cell": dict((event.get("envelope") or {}).get("control_cell") or CONTROL_CELL),
        "live_thesis_cut": LIVE_THESIS_CUT,
        "hypothetical_entries": hyp_reports,
        "capacity_projection": capacity,
        "note": "minimum-B report; no best-strategy claim",
    }


def _events_per_day_estimate(data_dir: Optional[str]) -> float:
    root = data_dir or os.getcwd()
    index_path = os.path.join(root, "research_events_v22.index.json")
    index = _load_event_index(index_path)
    n = len(index.get("events") or {})
    if n < 2:
        return 15.0
    return max(5.0, min(315.0, float(n)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="v2.2 replay-event-report for one event_id")
    parser.add_argument("event_id", help="event_id / trade_id")
    parser.add_argument("--data-dir", default=os.getcwd())
    parser.add_argument("--output", default="-")
    args = parser.parse_args(argv)
    event = find_event(args.data_dir, args.event_id)
    if not event:
        print(json.dumps({"error": "event not found", "event_id": args.event_id}))
        return 1
    report = replay_event_report(event, data_dir=args.data_dir)
    text = json.dumps(report, indent=2)
    if args.output == "-":
        print(text)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
