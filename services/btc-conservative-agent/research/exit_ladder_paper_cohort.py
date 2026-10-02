"""Paper-research cohort for the exit-ladder simulator.

The simulator replays executed *paper* trade paths under alternative ladder
profiles.  Gating it on ``REAL_COPY_PARAMETER_OPTIMISATION`` required Bitfinex
linkage, Bitfinex realized PnL and relay stop/ack evidence, so with the relay
disarmed (paper only) the cohort is empty by construction.  This cohort asks
the paper question instead: executed trades of the current registry tiles in
the current epoch, with an explicitly complete replay path and a supported
terminal provenance.  Retired-tile trades stay quarantined.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.analysis_eligibility import _EXCLUDED_PROVENANCE

SCHEMA = "paper_exit_ladder_cohort_v1"
COHORT = "PAPER_RESEARCH_CURRENT_REGISTRY_EPOCH"
SIGNAL_REPLAY_FILE = "signal_replay.jsonl"


def active_registry_lanes() -> dict[str, str]:
    """Return {id_prefix: lane} for the canonical active-tile registry."""
    from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

    return {
        str(ACTIVE_TILE_REGISTRY[lane].get("id_prefix") or "").lower(): str(lane).upper()
        for lane in ACTIVE_TILE_ORDER
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.casefold() in {"nan", "none", "null"} else text


def replay_keys(trade_id: str) -> tuple[str, ...]:
    tid = str(trade_id or "").strip()
    if not tid:
        return ()
    return (tid,) if tid.startswith("rev-") else (tid, f"rev-{tid}")


def _replay_for(trade_id: str, replays: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return next((replays[key] for key in replay_keys(trade_id) if key in replays), None)


def classify_trade(
    row: Mapping[str, Any],
    replays: Mapping[str, Mapping[str, Any]],
    *,
    epoch_id: str | None,
    lanes_by_prefix: Mapping[str, str],
) -> list[str]:
    """Return exclusion reasons; an empty list means eligible."""
    trade_id = _text(row.get("trade_id")) or ""
    lane = (_text(row.get("research_lane")) or "").upper()
    prefix = trade_id.split("-", 1)[0].lower()
    if not trade_id:
        return ["CANONICAL_IDENTITY_MISSING"]
    if lane not in set(lanes_by_prefix.values()) or lanes_by_prefix.get(prefix) != lane:
        return ["NON_REGISTRY_LANE"]
    reasons = []
    row_epoch = _text(row.get("epoch_id")) or _text(row.get("research_collection_id"))
    if epoch_id is None:
        reasons.append("CURRENT_EPOCH_UNKNOWN")
    elif row_epoch != epoch_id:
        reasons.append("OTHER_OR_MISSING_EPOCH")
    if not (_text(row.get("policy_signature")) and (
        _text(row.get("cfg_policy_version")) or _text(row.get("policy_version"))
    )):
        reasons.append("POLICY_IDENTITY_MISSING")
    replay = _replay_for(trade_id, replays)
    if replay is None:
        reasons.append("REPLAY_MISSING")
    else:
        if replay.get("replay_complete") is not True:
            reasons.append("REPLAY_INCOMPLETE")
        provenance = str(replay.get("terminal_provenance") or "").upper()
        if not provenance:
            reasons.append("TERMINAL_PROVENANCE_MISSING")
        elif provenance in _EXCLUDED_PROVENANCE:
            reasons.append("EXCLUDED_TERMINAL_PROVENANCE")
    return reasons


def build_paper_exit_ladder_cohort(
    trade_rows: Iterable[Mapping[str, Any]],
    replays: Mapping[str, Mapping[str, Any]],
    *,
    epoch_id: str | None,
    lanes_by_prefix: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    lanes_by_prefix = dict(lanes_by_prefix if lanes_by_prefix is not None else active_registry_lanes())
    eligible: list[str] = []
    exclusions: Counter[str] = Counter()
    registry_trades = 0
    registry_with_replay = 0
    seen: set[str] = set()
    for row in trade_rows:
        trade_id = _text(row.get("trade_id")) or ""
        if trade_id in seen:
            continue
        seen.add(trade_id)
        reasons = classify_trade(row, replays, epoch_id=epoch_id, lanes_by_prefix=lanes_by_prefix)
        if "NON_REGISTRY_LANE" not in reasons and "CANONICAL_IDENTITY_MISSING" not in reasons:
            registry_trades += 1
            if _replay_for(trade_id, replays) is not None:
                registry_with_replay += 1
        if reasons:
            exclusions.update(reasons)
        else:
            eligible.append(trade_id)
    return {
        "schema": SCHEMA,
        "cohort": COHORT,
        "epoch_id": epoch_id,
        "active_registry_lanes": sorted(set(lanes_by_prefix.values())),
        "relay_independent": True,
        "note": (
            "Paper research cohort: does not require Bitfinex linkage or relay "
            "evidence. Retired-tile trades are excluded as NON_REGISTRY_LANE."
        ),
        "evidence_rows": len(seen),
        "current_registry_trades": registry_trades,
        "current_registry_trades_with_replay": registry_with_replay,
        "eligible_trade_ids": sorted(eligible),
        "eligible_count": len(eligible),
        "exclusion_reason_counts": dict(sorted(exclusions.items())),
    }


def filter_for_exit_ladder(trades, replays, *, epoch_id: str | None):
    """Return (trades_df, replays, receipt) restricted to the paper cohort."""
    has_rows = trades is not None and not getattr(trades, "empty", True) and "trade_id" in trades.columns
    rows = trades.astype(object).where(trades.notna(), None).to_dict("records") if has_rows else []
    receipt = build_paper_exit_ladder_cohort(rows, replays or {}, epoch_id=epoch_id)
    allowed = set(receipt["eligible_trade_ids"])
    filtered_trades = trades[trades["trade_id"].astype(str).isin(allowed)].copy() if has_rows else trades
    allowed_keys = {key for tid in allowed for key in replay_keys(tid)}
    filtered_replays = {
        str(key): value for key, value in (replays or {}).items() if str(key) in allowed_keys
    }
    return filtered_trades, filtered_replays, receipt


def signal_replay_paths(data_dir: str | Path) -> list[Path]:
    active = Path(data_dir) / SIGNAL_REPLAY_FILE
    rotated = []
    for path in active.parent.glob(active.name + ".*"):
        suffix = path.name[len(active.name) + 1:]
        if path.is_file() and suffix.isdigit():
            rotated.append((int(suffix), path))
    return [path for _, path in sorted(rotated)] + ([active] if active.is_file() else [])


def load_replay_index(data_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Tick-free replay facts per trade_id; completion only from explicit True."""
    index: dict[str, dict[str, Any]] = {}
    for path in signal_replay_paths(data_dir):
        with open(path, "r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or not row.get("trade_id"):
                    continue
                tid = str(row["trade_id"])
                prior = index.get(tid) or {}
                ticks = row.get("ticks") if isinstance(row.get("ticks"), list) else []
                index[tid] = {
                    "trade_id": tid,
                    "lane": row.get("lane") or prior.get("lane"),
                    "replay_complete": prior.get("replay_complete") is True or row.get("replay_complete") is True,
                    "terminal_provenance": row.get("terminal_provenance") or prior.get("terminal_provenance"),
                    "replay_completion_reason": (
                        row.get("replay_completion_reason") or prior.get("replay_completion_reason")
                    ),
                    "censored": bool(prior.get("censored")) or row.get("censored") is True,
                    "tick_count": max(int(prior.get("tick_count") or 0), len(ticks)),
                    "start_ts": row.get("start_ts") or prior.get("start_ts"),
                }
    return index
