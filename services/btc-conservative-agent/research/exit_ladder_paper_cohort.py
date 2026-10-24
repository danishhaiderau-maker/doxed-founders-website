"""Paper-research cohort for the exit-ladder simulator.

The simulator replays executed *paper* trade paths under alternative ladder
profiles.  Gating it on ``REAL_COPY_PARAMETER_OPTIMISATION`` required Bitfinex
linkage, Bitfinex realized PnL and relay stop/ack evidence, so with the relay
disarmed (paper only) the cohort is empty by construction.  This cohort asks
the paper question instead: executed trades of the current registry tiles in
the current epoch, with an explicitly complete replay path and a supported
terminal provenance.  Retired-tile trades stay quarantined.

R17 / PRIME RULE: a trade is countable only when its whole replay span
(entry through the last recorded tick) is covered by real receive-stamped
``bfx_book_10l_1s`` book rows and ``bfx_trades`` prints (the same coverage
rule the protection replay uses, ``research_v3_book_walk``).  Anything else
is ``R17_UNPROVEN`` and is excluded from the cohort, never counted.  When no
book coverage was loaded every trade is R17_UNPROVEN (fail closed).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from research.analysis_eligibility import _EXCLUDED_PROVENANCE

# v2 (HM #847 C4): the receipt carries book_coverage_loaded / r17_unproven_count. A v1 receipt (or any receipt
# without book_coverage_loaded=True) predates the R17 book-coverage gate and is recomputed, never trusted.
SCHEMA = "paper_exit_ladder_cohort_v2"
COHORT = "PAPER_RESEARCH_CURRENT_REGISTRY_EPOCH"
SIGNAL_REPLAY_FILE = "signal_replay.jsonl"
R17_UNPROVEN = "R17_UNPROVEN"
BookCoverage = Callable[[float, float], Mapping[str, Any]]


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


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def replay_span(replay: Mapping[str, Any]) -> tuple[float, float] | None:
    """Absolute (start, end) seconds of a replay path, or None when unknowable."""
    start = _num(replay.get("start_ts"))
    if start is None or start <= 0:
        return None
    end = _num(replay.get("end_ts"))
    if end is None:
        rel = [_num(t.get("t")) for t in replay.get("ticks") or [] if isinstance(t, Mapping)]
        rel = [t for t in rel if t is not None]
        end = start + max(rel) if rel else None
    return (start, end) if end is not None and end >= start else None


def r17_unproven_reason(replay: Mapping[str, Any], book_coverage: BookCoverage | None) -> str | None:
    """None when the replay span is fully book-covered, else the R17_UNPROVEN reason."""
    if book_coverage is None:
        return "BOOK_COVERAGE_NOT_LOADED"
    span = replay_span(replay)
    if span is None:
        return "REPLAY_SPAN_UNKNOWN"
    coverage = book_coverage(*span)
    return None if coverage.get("covered") is True else str(coverage.get("reason") or "NOT_COVERED")


def classify_trade(
    row: Mapping[str, Any],
    replays: Mapping[str, Mapping[str, Any]],
    *,
    epoch_id: str | None,
    lanes_by_prefix: Mapping[str, str],
    book_coverage: BookCoverage | None = None,
    unproven_reasons: Counter[str] | None = None,
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
        unproven = r17_unproven_reason(replay, book_coverage)
        if unproven is not None:
            reasons.append(R17_UNPROVEN)
            if unproven_reasons is not None:
                unproven_reasons[unproven] += 1
    return reasons


def build_paper_exit_ladder_cohort(
    trade_rows: Iterable[Mapping[str, Any]],
    replays: Mapping[str, Mapping[str, Any]],
    *,
    epoch_id: str | None,
    lanes_by_prefix: Mapping[str, str] | None = None,
    book_coverage: BookCoverage | None = None,
    coverage_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    lanes_by_prefix = dict(lanes_by_prefix if lanes_by_prefix is not None else active_registry_lanes())
    eligible: list[str] = []
    exclusions: Counter[str] = Counter()
    registry_trades = 0
    registry_with_replay = 0
    seen: set[str] = set()
    unproven_reasons: Counter[str] = Counter()
    for row in trade_rows:
        trade_id = _text(row.get("trade_id")) or ""
        if trade_id in seen:
            continue
        seen.add(trade_id)
        reasons = classify_trade(row, replays, epoch_id=epoch_id, lanes_by_prefix=lanes_by_prefix,
                                 book_coverage=book_coverage, unproven_reasons=unproven_reasons)
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
        "r17_unproven_count": int(exclusions.get(R17_UNPROVEN, 0)),
        "r17_unproven_reasons": dict(sorted(unproven_reasons.items())),
        "book_coverage_loaded": book_coverage is not None,
        "book_coverage_receipt": dict(coverage_receipt or {}),
        # RK #847 cond 2: the interim C2 5 s trade-gap rule runs only inside the protection screen's book-walk
        # stop replay; this cohort filter replays no stops, so it flags nothing (stamped explicitly, not 0).
        "c2_stop_unobservable_trade_gap_cells": None,
        "c2_stop_unobservable_rule": "NOT_APPLIED_NO_BOOK_WALK_STOP_REPLAY",
    }


def load_book_coverage(data_dir: str | Path | None, replays: Mapping[str, Mapping[str, Any]]):
    """(coverage callable, receipt) over the replay spans, or (None, receipt) when not loadable."""
    if data_dir is None:
        return None, {"status": "NO_DATA_DIR"}
    spans = [span for span in (replay_span(r) for r in replays.values() if isinstance(r, Mapping)) if span]
    if not spans:
        return None, {"status": "NO_REPLAY_SPANS"}
    from research_v3_book_walk import load_book_walk

    tape = load_book_walk(data_dir, spans)
    receipt = {"status": "LOADED", **{k: v for k, v in tape.receipt.items() if not isinstance(v, (list, dict))}}
    return tape.coverage, receipt


def filter_for_exit_ladder(trades, replays, *, epoch_id: str | None, data_dir: str | Path | None = None,
                           book_coverage: BookCoverage | None = None):
    """Return (trades_df, replays, receipt) restricted to the paper cohort.

    Trades whose replay span lacks book coverage are R17_UNPROVEN and dropped.
    """
    has_rows = trades is not None and not getattr(trades, "empty", True) and "trade_id" in trades.columns
    rows = trades.astype(object).where(trades.notna(), None).to_dict("records") if has_rows else []
    coverage_receipt = {"status": "SUPPLIED"} if book_coverage is not None else None
    if book_coverage is None:
        book_coverage, coverage_receipt = load_book_coverage(data_dir, replays or {})
    receipt = build_paper_exit_ladder_cohort(rows, replays or {}, epoch_id=epoch_id,
                                             book_coverage=book_coverage, coverage_receipt=coverage_receipt)
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


def _end_ts(row: Mapping[str, Any]) -> float | None:
    span = replay_span({"start_ts": row.get("start_ts"), "ticks": row.get("ticks") or []})
    return span[1] if span else None


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
                    "end_ts": max(
                        (e for e in (_end_ts(row), _num(prior.get("end_ts"))) if e is not None), default=None,
                    ),
                }
    return index
