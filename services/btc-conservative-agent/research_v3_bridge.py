"""Dual-write bridge from immutable v2.2 events into normalized V3 ledgers."""
from __future__ import annotations

from typing import Any, Mapping
import copy
from datetime import datetime
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import time

from research_v3_contract import COLLECTOR_VERSION
from research_v3_store import V3EvidenceStore
from research_v3_contract import canonical_json, normalize_lifecycle_outcome


_OHLCV_FIELDS = ("t", "o", "h", "l", "c", "v")
DEFAULT_DECLARED_ENTRY_TTL_SEC = 30 * 60
ENTRY_RECONCILIATION_ALLOWANCE_SEC = 3 * 60
PRE_SIGNAL_CONTEXT_SEC = 3 * 60


def _normalize_market_rows(rows: list[Any], *, timeframe: str) -> list[dict[str, Any]]:
    """Give compact production tape rows explicit, hash-stable field names."""
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if isinstance(row, Mapping):
            normalized.append(dict(row))
        elif timeframe == "1m" and isinstance(row, (list, tuple)) and len(row) >= 6:
            normalized.append(dict(zip(_OHLCV_FIELDS, row[:6])))
        else:
            raise ValueError(f"UNSUPPORTED_CANONICAL_{timeframe.upper()}_ROW:{index}")
    return normalized


def _first(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


def _timestamp(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None


def _paper_atr14_pct_3m(*sources: Mapping[str, Any]) -> float | None:
    """Read an explicitly observed 3-minute ATR receipt without inventing one."""
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        context = source.get("context") if isinstance(source.get("context"), Mapping) else {}
        ai_input = source.get("ai_input") if isinstance(source.get("ai_input"), Mapping) else {}
        # Persisted AI inputs wrap this receipt under ``context``. Shared
        # in-process child-lane signals carry the same observed receipt at the
        # top level. Accept both shapes without deriving or fabricating ATR.
        direct_cycle = source.get("cycle_3m_universe")
        nested_cycle = context.get("cycle_3m_universe")
        ai_input_cycle = ai_input.get("cycle_3m_universe")
        cycle = (
            direct_cycle
            if isinstance(direct_cycle, Mapping)
            else nested_cycle
            if isinstance(nested_cycle, Mapping)
            else ai_input_cycle
            if isinstance(ai_input_cycle, Mapping)
            else {}
        )
        research = source.get("research_feature_snapshot") if isinstance(source.get("research_feature_snapshot"), Mapping) else {}
        market = research.get("market_context") if isinstance(research.get("market_context"), Mapping) else {}
        for value in (
            source.get("atr14_pct_at_fill"),
            source.get("atr14_pct_3m"),
            source.get("atr14_pct"),
            cycle.get("atr14_pct_3m"),
            research.get("atr14_pct_3m"),
            market.get("atr14_pct_3m"),
        ):
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
    return None


def _paper_fill_execution_receipt(
    order: Mapping[str, Any], position: Mapping[str, Any], signal: Mapping[str, Any]
) -> dict[str, Any]:
    """Freeze the exact conservative fill evidence instead of implying it."""
    evidence = order.get("source_order_market_evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    observation = evidence.get("latest_observation")
    observation = observation if isinstance(observation, Mapping) else {}
    gate = order.get("venue_fill_gate")
    gate = gate if isinstance(gate, Mapping) else {}
    source = observation or gate
    verdict = str(_first(source.get("verdict"), source.get("gate_verdict")) or "").upper()

    def number(*values: Any) -> float | None:
        value = _first(*values)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    requested = number(order.get("requested_qty"), order.get("qty"))
    filled = number(position.get("qty"), order.get("filled_qty"), order.get("qty"))
    remaining = max(0.0, requested - filled) if requested is not None and filled is not None else None
    required = (
        source.get("best_bid"), source.get("best_ask"),
        source.get("side_correct_executable_quote"), source.get("visible_executable_qty"),
        source.get("book_ts"),
    )
    supported = verdict == "EXECUTABLE" and all(value not in (None, "") for value in required)
    fill_sim = order.get("fill_sim") if isinstance(order.get("fill_sim"), Mapping) else {}
    return {
        "execution_basis": "CONSERVATIVE_BBO_DEPTH" if supported else "UNSUPPORTED",
        "conservative_fill_supported": supported,
        "fill_gate_policy": _first(source.get("gate_policy"), evidence.get("gate_policy")),
        "fill_gate_verdict": verdict or None,
        "activation_ts": _first(source.get("activation_ts"), evidence.get("activation_ts")),
        "limit_generation": _first(source.get("generation"), order.get("limit_generation"), 0),
        "original_limit_price": number(source.get("original_limit_price"), order.get("orig_limit")),
        "current_limit_price": number(source.get("current_limit_price"), order.get("limit_price")),
        "requested_qty": requested,
        "filled_qty": filled,
        "remaining_qty": remaining,
        "partial_fill": bool(order.get("partial_fill")) or bool(remaining and remaining > 0),
        "book_ts": _first(source.get("book_ts"), source.get("market_ts")),
        "book_age_sec": number(source.get("book_age_sec")),
        "best_bid": number(source.get("best_bid")),
        "best_ask": number(source.get("best_ask")),
        "side_correct_executable_quote": number(source.get("side_correct_executable_quote")),
        "visible_executable_qty": number(source.get("visible_executable_qty")),
        "recent_aggressor_qty": number(source.get("recent_aggressor_qty")),
        "entry_slippage_from_signal_usd": number(
            position.get("entry_slippage"), order.get("entry_slippage")
        ),
        "book_walk_slippage_usd": number(fill_sim.get("slippage_usd"), order.get("book_slippage_usd")),
        "fill_time_revalidation": copy.deepcopy(
            order.get("fill_time_revalidation")
            if isinstance(order.get("fill_time_revalidation"), Mapping)
            else {"performed": False, "result": "UNAVAILABLE"}
        ),
    }


def _normalized_partial_exits(outcome: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep reduce-only legs comparable without guessing missing quantities/PnL."""
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(outcome.get("partial_exit_receipts") or []):
        if not isinstance(raw, Mapping):
            continue
        rows.append({
            "sequence": index + 1, "ts": _first(raw.get("ts"), raw.get("observed_ts")),
            "reason": _first(raw.get("reason"), raw.get("exit_reason"), raw.get("action")),
            "price": raw.get("price"), "closed_qty": raw.get("closed_qty"),
            "remaining_fraction": raw.get("remaining_fraction"),
            "realized_gross_usd": raw.get("realized_gross_usd"),
            "realized_net_usd": raw.get("realized_net_usd"),
        })
    return rows


def _observed_context(source: Mapping[str, Any], *, phase: str) -> dict[str, Any]:
    """Normalize already-observed context; unavailable values remain null."""
    prefix = "entry" if phase == "ENTRY" else "exit"
    nested = source.get(f"{prefix}_context")
    nested = nested if isinstance(nested, Mapping) else {}
    return {
        "phase": phase,
        "observed_ts": _first(nested.get("observed_ts"), source.get(f"{prefix}_context_ts")),
        "atr14_pct_3m": _first(nested.get("atr14_pct_3m"), source.get(f"atr14_pct_at_{prefix}")),
        "regime": _first(nested.get("regime"), source.get(f"{prefix}_regime"), source.get("regime") if phase == "ENTRY" else None),
        "adx": _first(nested.get("adx"), source.get(f"adx_at_{prefix}")),
        "sr_state": _first(nested.get("sr_state"), source.get(f"sr_state_at_{prefix}"), source.get("sr_state") if phase == "ENTRY" else None),
        "dist_to_support": _first(nested.get("dist_to_support"), source.get(f"distance_to_support_at_{prefix}"), source.get("distance_to_support") if phase == "ENTRY" else None),
        "dist_to_resistance": _first(nested.get("dist_to_resistance"), source.get(f"distance_to_resistance_at_{prefix}"), source.get("distance_to_resistance") if phase == "ENTRY" else None),
        "ema9": _first(nested.get("ema9"), source.get(f"ema9_at_{prefix}")),
        "ema21": _first(nested.get("ema21"), source.get(f"ema21_at_{prefix}")),
        "ema200": _first(nested.get("ema200"), source.get(f"ema200_at_{prefix}")),
    }


def _paper_path_receipt(rows: list[Mapping[str, Any]], *, direction: str, entry_price: Any, fill_ts: Any) -> dict[str, Any]:
    """Derive extrema timing only from the frozen observed path."""
    empty = {"basis": "UNAVAILABLE", "mfe_pct": None, "mae_pct": None,
             "time_to_mfe_sec": None, "time_to_mae_sec": None}
    try:
        entry, start = float(entry_price), float(fill_ts)
    except (TypeError, ValueError):
        return empty
    if entry <= 0 or not rows:
        return empty
    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    samples = []
    for row in rows:
        ts = _timestamp(_first(row.get("ts"), row.get("bucket_ts")))
        try:
            price = float(_first(row.get("price"), row.get("last")))
        except (TypeError, ValueError):
            continue
        if ts is not None and ts >= start:
            samples.append((ts, ((price - entry) / entry) * 100.0 * sign))
    if not samples:
        return empty
    mfe_ts, mfe = max(samples, key=lambda item: item[1])
    mae_ts, mae = min(samples, key=lambda item: item[1])
    return {"basis": "OBSERVED_1S_PRICE_PATH", "mfe_pct": round(mfe, 6),
            "mae_pct": round(mae, 6), "time_to_mfe_sec": round(mfe_ts - start, 3),
            "time_to_mae_sec": round(mae_ts - start, 3)}


def _paper_market_segment(data_dir: str, *, start_ts: float, end_ts: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load the immutable one-second tape covering one observed paper path."""
    source = Path(data_dir) / "market_microstructure_1s.jsonl"
    rows_by_ts: dict[float, dict[str, Any]] = {}
    parse_errors = 0
    if source.is_file() and end_ts >= start_ts:
        with source.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    parse_errors += 1
                    continue
                if not isinstance(raw, Mapping):
                    continue
                ts = _timestamp(_first(raw.get("bucket_ts"), raw.get("ts"), raw.get("t")))
                price = _first(raw.get("last"), raw.get("price"), raw.get("mark"))
                if ts is None or price in (None, "") or ts < start_ts or ts > end_ts:
                    continue
                row = dict(raw)
                # Candidate replay consumes explicit ts/price while the full
                # BBO/depth row remains available for conservative fills.
                row["ts"] = ts
                row["price"] = float(price)
                rows_by_ts[ts] = row
    rows = [rows_by_ts[key] for key in sorted(rows_by_ts)]
    times = list(sorted(rows_by_ts))
    gaps = [right - left for left, right in zip(times, times[1:])]
    max_gap = max(gaps) if gaps else None
    coverage = {
        "schema": "paper_market_segment_coverage_v1",
        "requested_start_ts": float(start_ts),
        "requested_end_ts": float(end_ts),
        "observed_start_ts": times[0] if times else None,
        "observed_end_ts": times[-1] if times else None,
        "row_count": len(rows),
        "max_gap_sec": max_gap,
        "two_second_or_better": bool(rows) and (max_gap is None or max_gap <= 2.0),
        "parse_errors": parse_errors,
    }
    return rows, coverage


@lru_cache(maxsize=512)
def _pre_signal_market_segment(
    data_dir: str, signal_ts: float,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Freeze one shared, causal lookback without implying future path data."""
    rows, coverage = _paper_market_segment(
        data_dir,
        start_ts=float(signal_ts) - PRE_SIGNAL_CONTEXT_SEC,
        end_ts=float(signal_ts),
    )
    receipt = dict(coverage)
    receipt.update({
        "context_role": "PRE_SIGNAL_ONLY",
        "lookback_sec": PRE_SIGNAL_CONTEXT_SEC,
        "future_exit_path_included": False,
    })
    return tuple(rows), receipt


def _causal_identity(event_id: str, *sources: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the same causal episode for provisional and live paper rows."""
    shared = str(_first(*(source.get("shared_ai_call_id") for source in sources)) or "").strip()
    stable_episode_id = str(_first(*(source.get("event_episode_id") for source in sources)) or "").strip()
    symbol = str(_first(*(source.get("symbol") or source.get("pair") for source in sources), "BTCUSD")).upper()
    raw_direction = str(_first(*(source.get("raw_direction") for source in sources)) or "").upper()
    executed_direction = str(_first(*(source.get("executed_direction") or source.get("final_direction") or source.get("signal_dir") or source.get("dir") for source in sources)) or "UNKNOWN").upper()
    raw_direction = raw_direction or executed_direction
    if shared:
        # A shared AI call is the causal unit.  Symbol spellings may be
        # enriched later (for example BTCUSD -> tBTCF0:USTF0), and direction
        # treatments may differ by lane.  Neither is allowed to mint a second
        # episode for the same call.
        causal_key = f"shared:{shared}"
        episode_id = "episode-" + hashlib.sha256(causal_key.encode("utf-8")).hexdigest()[:20]
        grouping_basis = "SHARED_AI_CALL"
    else:
        episode_id, grouping_basis = stable_episode_id, "STABLE_EVENT_EPISODE"
    if not event_id or not episode_id:
        raise ValueError("V3_CAUSAL_IDENTITY_INCOMPLETE")
    return {"event_id": str(event_id), "episode_id": episode_id, "shared_ai_call_id": shared or None,
            "symbol": symbol, "raw_direction": raw_direction, "executed_direction": executed_direction,
            "grouping_basis": grouping_basis}


def _explicit_causal_ids(
    *, epoch_id: str, event_id: str, episode_id: str,
    include_schedule: bool = False, include_fill: bool = False,
    tape_id: str | None = None,
) -> dict[str, Any]:
    """Name real causal objects without inferring equivalence from time/price.

    These IDs identify the already-existing opportunity, paper-order schedule,
    primary fill slot and immutable tape object.  They are deliberately based
    only on their durable causal owner; a missing schedule/tape/fill remains
    missing rather than being reconstructed heuristically downstream.
    """
    ids: dict[str, Any] = {
        "opportunity_id": f"opportunity:{episode_id}",
    }
    if include_schedule:
        ids["schedule_id"] = f"schedule:{epoch_id}:{event_id}:paper-primary"
    if include_fill:
        ids["fill_id"] = f"fill:{epoch_id}:{event_id}:paper-primary"
    if tape_id:
        ids["tape_id"] = str(tape_id)
    return ids


def _paper_policy_identity(epoch_id: str, *sources: Mapping[str, Any]) -> dict[str, Any]:
    """Derive a lane-scoped identity instead of reusing the live CONTROL tag."""
    for source in sources:
        frozen_spec = source.get("paper_policy_spec")
        if (
            source.get("policy_identity_schema") == "paper_policy_identity_v3"
            and isinstance(frozen_spec, Mapping)
            and frozen_spec.get("policy_id")
        ):
            # The immutable spec is the authority.  Sparse lifecycle merges can
            # accidentally combine that spec with the base CONTROL signature
            # carried by a master signal.  Trusting the mismatched top-level
            # fields contaminated a terminal NO_ORDER row even though its
            # policy material still described the correct Patient lane.
            spec = copy.deepcopy(dict(frozen_spec))
            signature = "paper-policy-" + hashlib.sha256(
                canonical_json(spec).encode("utf-8")
            ).hexdigest()[:20]
            policy_epoch_id = "paper-policy-epoch-" + hashlib.sha256(
                f"{epoch_id}|{signature}".encode("utf-8")
            ).hexdigest()[:20]
            return {
                "policy_identity_schema": "paper_policy_identity_v3",
                "policy_id": str(spec["policy_id"]),
                "policy_signature": signature,
                "policy_epoch_id": policy_epoch_id,
                "base_policy_signature": (
                    spec.get("base_policy_signature")
                    or source.get("base_policy_signature")
                ),
                "base_policy_epoch_id": source.get("base_policy_epoch_id"),
                "paper_policy_spec": spec,
                "policy_execution_scope": "PAPER_RESEARCH_ONLY",
                "relay_capability": (
                    "RELAY_ELIGIBLE"
                    if bool(spec.get("relay_eligible"))
                    else "NOT_RELAY_ELIGIBLE"
                ),
            }
    research_lane = str(_first(*(source.get("research_lane") for source in sources)) or "").strip()
    policy_id = str(_first(
        *(source.get("policy_id") or source.get("raw_policy_id") for source in sources),
        research_lane,
        "UNSPECIFIED_PAPER_POLICY",
    ))
    base_signature = str(_first(*(source.get("policy_signature") for source in sources)) or "").strip()
    base_epoch = str(_first(*(source.get("policy_epoch_id") for source in sources)) or "").strip()
    relay_default = research_lane.upper() == "CONTINUOUS"
    chase_schedule = _first(*(source.get("research_chase_schedule") for source in sources)) or {}
    declared_entry_ttl_sec = _first(*(
        source.get("entry_ttl_sec") or source.get("signal_ttl_sec")
        for source in sources
    ), chase_schedule.get("entry_ttl_sec"), chase_schedule.get("ttl_sec"),
        DEFAULT_DECLARED_ENTRY_TTL_SEC)
    spec = {
        "schema": "paper_policy_identity_spec_v3",
        "policy_id": policy_id,
        "research_lane": research_lane or None,
        "entry_limit_policy": _first(*(source.get("entry_limit_policy") for source in sources)),
        "entry_offset_fraction": _first(*(
            source.get("entry_offset_fraction")
            if source.get("entry_offset_fraction") is not None
            else source.get("deterministic_entry_offset_pct")
            for source in sources
        )),
        "declared_entry_ttl_sec": float(declared_entry_ttl_sec),
        "entry_reconciliation_allowance_sec": ENTRY_RECONCILIATION_ALLOWANCE_SEC,
        "exit_config": _first(*(source.get("exit_config") for source in sources)),
        # V3 policy evidence describes the canonical local paper lifecycle.
        # Whether that lifecycle is eligible for a separately armed relay is
        # represented only by ``relay_eligible``.  Deriving this signed field
        # from sparse order dictionaries allowed a relay-capable lane to flip
        # from paper_only=true at decision time to false at submit time,
        # minting two policy signatures for one episode.
        "paper_only": True,
        "relay_eligible": bool(_first(*(source.get("relay_eligible") for source in sources), relay_default)),
        "base_policy_signature": base_signature or None,
    }
    signature = "paper-policy-" + hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()[:20]
    policy_epoch_id = "paper-policy-epoch-" + hashlib.sha256(
        f"{epoch_id}|{signature}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "policy_identity_schema": "paper_policy_identity_v3",
        "policy_id": policy_id,
        "policy_signature": signature,
        "policy_epoch_id": policy_epoch_id,
        "base_policy_signature": base_signature or None,
        "base_policy_epoch_id": base_epoch or None,
        # Persist the exact material that produced the signature.  Downstream
        # rows must not re-interpret sparse order dictionaries differently
        # from the original lane decision.
        "paper_policy_spec": spec,
        "policy_execution_scope": "PAPER_RESEARCH_ONLY",
        "relay_capability": (
            "RELAY_ELIGIBLE" if spec["relay_eligible"] else "NOT_RELAY_ELIGIBLE"
        ),
    }


def paper_policy_identity_for_sources(
    epoch_id: str, *sources: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the exact paper identity once for later fill/close attribution."""
    return copy.deepcopy(_paper_policy_identity(str(epoch_id), *sources))


def dual_write_lane_entry_resolution(
    source: Mapping[str, Any],
    *,
    lane: str,
    entry_resolution: str,
    exact_reason: str,
    epoch_id: str,
    data_dir: str,
    lane_policy: Mapping[str, Any] | None = None,
    observed_ts: float | None = None,
) -> dict[str, Any]:
    """Append one lane-scoped entry resolution without rewriting its verdict."""
    resolution = str(entry_resolution or "").strip().upper()
    if resolution not in {"AWAITING", "ORDER_SUBMITTED", "NO_ORDER"}:
        raise ValueError(f"V3_UNKNOWN_LANE_ENTRY_RESOLUTION:{resolution}")
    lane_name = str(lane or "").strip().upper()
    call_id = str(_first(source.get("shared_ai_call_id"), source.get("trade_id")) or "").strip()
    if not lane_name or not call_id:
        raise ValueError("V3_LANE_ENTRY_RESOLUTION_IDENTITY_INCOMPLETE")
    material = dict(source)
    material.update(dict(lane_policy or {}))
    material["research_lane"] = lane_name
    identity = _causal_identity(f"lane-entry:{lane_name}:{call_id}", material)
    policy = _paper_policy_identity(str(epoch_id), material)
    causal_ids = _explicit_causal_ids(
        epoch_id=str(epoch_id), event_id=identity["event_id"],
        episode_id=identity["episode_id"],
    )
    now_ts = float(observed_ts if observed_ts is not None else time.time())
    signal_ts = float(_first(
        source.get("signal_ts"), source.get("shared_ai_call_ts_epoch"),
        source.get("created_ts_ts"), now_ts,
    ))
    policy_spec = policy["paper_policy_spec"]
    deadline_ts = signal_ts + float(policy_spec["declared_entry_ttl_sec"]) + float(
        policy_spec["entry_reconciliation_allowance_sec"]
    )
    suffix = {
        "AWAITING": "awaiting", "ORDER_SUBMITTED": "submitted", "NO_ORDER": "no-order",
    }[resolution]
    terminal_no_order = resolution == "NO_ORDER"
    segment_refs = list(source.get("market_context_segment_refs") or [])
    segment_coverage = dict(source.get("market_context_segment_coverage") or {})
    row = {
        "record_id": (
            f"lifecycle:{identity['episode_id']}:{policy['policy_signature']}:"
            f"{lane_name}:lane-entry:{suffix}"
        ),
        "episode_id": identity["episode_id"],
        "event_id": identity["event_id"],
        "shared_ai_call_id": identity["shared_ai_call_id"],
        "research_lane": lane_name,
        "resolution_scope": "LANE_ENTRY",
        "entry_resolution": resolution,
        "entry_resolution_terminal": resolution in {"ORDER_SUBMITTED", "NO_ORDER"},
        "exact_reason": str(exact_reason or "UNSPECIFIED"),
        "observed_ts": now_ts,
        "resolution_deadline_ts": deadline_ts,
        "observation_status": resolution,
        "outcome_state": "NO_TRADE" if terminal_no_order else "CENSORED",
        "terminal": terminal_no_order,
        "ranking_eligible": False,
        "ranking_blocker": "NO_ORDER" if terminal_no_order else "PATH_NOT_MATURED",
        "market_context_segment_refs": segment_refs,
        "market_context_segment_coverage": segment_coverage,
        **causal_ids,
        **policy,
    }
    write = V3EvidenceStore(data_dir, epoch_id=str(epoch_id)).append("lifecycle", row)
    return {
        "schema": "v3_lane_entry_resolution_receipt_v1", "epoch_id": str(epoch_id),
        **identity, "entry_resolution": resolution, "write": write,
    }


def dual_write_lane_decision(
    source: Mapping[str, Any],
    *,
    lane: str,
    policy_decision: str,
    execution_disposition: str,
    exact_reason: str,
    epoch_id: str,
    data_dir: str,
    lane_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one immediate, causal per-lane verdict for every shared call.

    A decision is evidence even when it correctly creates no order.  Keeping
    this separate from ``order_intent`` prevents REJECT/disabled/filter paths
    from disappearing or being misrepresented as zero-PnL trades.
    """
    lane_name = str(lane or "").strip().upper()
    call_id = str(_first(source.get("shared_ai_call_id"), source.get("trade_id")) or "").strip()
    if not lane_name or not call_id:
        raise ValueError("V3_LANE_DECISION_IDENTITY_INCOMPLETE")
    material = dict(source)
    material.update(dict(lane_policy or {}))
    material["research_lane"] = lane_name
    event_id = f"lane-decision:{lane_name}:{call_id}"
    identity = _causal_identity(event_id, material)
    policy = _paper_policy_identity(str(epoch_id), material)
    causal_ids = _explicit_causal_ids(
        epoch_id=str(epoch_id), event_id=event_id, episode_id=identity["episode_id"],
    )
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    signal_ts = float(_first(
        source.get("signal_ts"), source.get("shared_ai_call_ts_epoch"),
        source.get("created_ts_ts"), 0,
    ) or 0)
    policy_decision = str(policy_decision or "UNKNOWN").strip().upper()
    execution_disposition = str(execution_disposition or "UNKNOWN").strip().upper()
    outcome_state = (
        "REJECTED" if policy_decision in {"REJECT", "ERROR"}
        else "NO_TRADE" if execution_disposition != "ORDER_ELIGIBLE"
        else "CENSORED"
    )
    segment_refs: list[dict[str, Any]] = []
    segment_writes: list[dict[str, Any]] = []
    segment_coverage: dict[str, Any] = {
        "context_role": "PRE_SIGNAL_ONLY",
        "lookback_sec": PRE_SIGNAL_CONTEXT_SEC,
        "future_exit_path_included": False,
        "row_count": 0,
        "two_second_or_better": False,
        "reason": "SIGNAL_TIMESTAMP_MISSING",
    }
    if signal_ts > 0:
        segment_rows, segment_coverage = _pre_signal_market_segment(
            str(Path(data_dir).resolve()), signal_ts,
        )
        if segment_rows:
            segment_ref = store.put_market_segment(
                source="LIVE_MICROSTRUCTURE_1S_PRE_SIGNAL",
                symbol=identity["symbol"], timeframe="1s",
                start_ts=signal_ts - PRE_SIGNAL_CONTEXT_SEC,
                end_ts=signal_ts, rows=segment_rows,
            )
            segment_refs.append(segment_ref)
            context_event_id = f"market-context:{identity['episode_id']}"
            context_causal_ids = _explicit_causal_ids(
                epoch_id=str(epoch_id), event_id=context_event_id,
                episode_id=identity["episode_id"],
                tape_id=f"tape:{segment_ref['sha256']}",
            )
            segment_writes.append(store.append("market_segment", {
                "record_id": f"market-context:{identity['episode_id']}:{segment_ref['sha256']}",
                "event_id": context_event_id,
                "episode_id": identity["episode_id"],
                "shared_ai_call_id": identity["shared_ai_call_id"],
                "context_role": "PRE_SIGNAL_ONLY",
                "segment_ref": segment_ref,
                "coverage": segment_coverage,
                **context_causal_ids,
            }))
    opportunity = store.append("opportunity", {
        "record_id": f"opportunity:{identity['episode_id']}",
        "episode_id": identity["episode_id"],
        "shared_ai_call_id": identity["shared_ai_call_id"],
        "signal_ts": signal_ts,
        "symbol": identity["symbol"],
        "raw_direction": identity["raw_direction"],
        "feature_snapshot_at_signal": source.get("feature_snapshot_at_signal") or {},
        "market_context_segment_refs": segment_refs,
        "market_context_segment_coverage": segment_coverage,
        "grouping_basis": identity["grouping_basis"],
        "collector_version": COLLECTOR_VERSION,
        **causal_ids,
    })
    decision = store.append("decision", {
        "record_id": f"decision:{identity['episode_id']}:{policy['policy_signature']}:LANE_POLICY_VERDICT",
        "episode_id": identity["episode_id"],
        "event_id": event_id,
        "shared_ai_call_id": identity["shared_ai_call_id"],
        "decision_stage": "LANE_POLICY_VERDICT",
        "research_lane": lane_name,
        "policy_decision": policy_decision,
        "execution_disposition": execution_disposition,
        "outcome_state": outcome_state,
        "exact_reason": str(exact_reason or "UNSPECIFIED"),
        "executed_direction": identity["executed_direction"],
        "raw_ai_decision": source.get("raw_ai_decision"),
        "long_score": source.get("long_score"),
        "short_score": source.get("short_score"),
        "score_gap": source.get("score_gap"),
        "paper_only": bool(policy["paper_policy_spec"]["paper_only"]),
        "relay_eligible": bool(policy["paper_policy_spec"]["relay_eligible"]),
        "order_intent_expected": execution_disposition == "ORDER_ELIGIBLE",
        "decision_ts": signal_ts,
        "resolution_deadline_ts": signal_ts + float(policy["paper_policy_spec"]["declared_entry_ttl_sec"]) + float(policy["paper_policy_spec"]["entry_reconciliation_allowance_sec"]),
        "market_context_segment_refs": segment_refs,
        "market_context_segment_coverage": segment_coverage,
        **causal_ids,
        **policy,
    })
    resolution_material = dict(material)
    resolution_material["market_context_segment_refs"] = segment_refs
    resolution_material["market_context_segment_coverage"] = segment_coverage
    resolution = dual_write_lane_entry_resolution(
        resolution_material, lane=lane_name,
        entry_resolution="AWAITING" if execution_disposition == "ORDER_ELIGIBLE" else "NO_ORDER",
        exact_reason="ORDER_ELIGIBLE_AWAITING_EXECUTION" if execution_disposition == "ORDER_ELIGIBLE" else exact_reason,
        epoch_id=str(epoch_id), data_dir=data_dir, lane_policy=lane_policy,
        observed_ts=signal_ts,
    )
    return {
        "schema": "v3_lane_decision_receipt_v1",
        "epoch_id": str(epoch_id),
        **identity,
        "writes": [opportunity, *segment_writes, decision, resolution["write"]],
        "store_verification": store.verify(),
    }


def dual_write_paper_order_intent(order: Mapping[str, Any], signal: Mapping[str, Any], *, epoch_id: str, data_dir: str) -> dict[str, Any]:
    """Write an actual paper order intent immediately, before path maturity."""
    event_id = str(_first(order.get("trade_id"), signal.get("trade_id")) or "")
    identity = _causal_identity(event_id, signal, order)
    policy = _paper_policy_identity(str(epoch_id), signal, order)
    schedule = order.get("research_chase_schedule") or signal.get("research_chase_schedule")
    schedule_available = isinstance(schedule, Mapping) and schedule.get("authoritative") is True
    causal_ids = _explicit_causal_ids(
        epoch_id=str(epoch_id), event_id=event_id, episode_id=identity["episode_id"],
        include_schedule=schedule_available,
    )
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    signal_ts = float(_first((signal.get("timing") or {}).get("signal_ts"), signal.get("created_ts_ts"), order.get("signal_created_ts"), order.get("created_ts"), 0) or 0)
    opportunity = store.append("opportunity", {
        "record_id": f"opportunity:{identity['episode_id']}", "episode_id": identity["episode_id"],
        "shared_ai_call_id": identity["shared_ai_call_id"], "signal_ts": signal_ts,
        "symbol": identity["symbol"], "raw_direction": identity["raw_direction"],
        "feature_snapshot_at_signal": signal.get("research_feature_snapshot") or {},
        "grouping_basis": identity["grouping_basis"], "collector_version": COLLECTOR_VERSION,
        **causal_ids,
    })
    atr14_pct_at_signal = _paper_atr14_pct_3m(order, signal)
    signal_price = _first(order.get("signal_price"), signal.get("signal_price"))
    limit_price = _first(order.get("limit_price"), order.get("price"))
    entry_policy_id = str(
        policy["paper_policy_spec"].get("entry_limit_policy")
        or policy.get("policy_id")
        or ""
    )
    offset_fraction = _first(
        policy["paper_policy_spec"].get("entry_offset_fraction"),
        order.get("entry_offset_fraction"),
        signal.get("entry_offset_fraction"),
    )
    if offset_fraction is None:
        try:
            if float(signal_price) > 0:
                offset_fraction = abs(float(limit_price) - float(signal_price)) / float(signal_price)
        except (TypeError, ValueError):
            offset_fraction = None
    entry_children = []
    if entry_policy_id:
        entry_children.append({
            "entry_policy_id": entry_policy_id,
            "offset_pct": (float(offset_fraction) * 100.0) if offset_fraction is not None else None,
            "chase_id": entry_policy_id,
            "fill_ts": None,
            "fill_price": None,
            "fill_model": None,
        })
    intent = store.append("order_intent", {
        "record_id": f"order-intent:{event_id}:paper-submit", "episode_id": identity["episode_id"], "event_id": event_id,
        "shared_ai_call_id": identity["shared_ai_call_id"],
        "intent_kind": "ACTUAL_PAPER_LIMIT_SUBMIT", "submitted_ts": _first(order.get("created_ts"), order.get("order_created_ts")),
        "signal_price": signal_price,
        "limit_price": limit_price, "requested_qty": order.get("qty"),
        "executed_direction": identity["executed_direction"], "research_lane": _first(order.get("research_lane"), signal.get("research_lane")),
        "paper_only": bool(policy["paper_policy_spec"]["paper_only"]),
        "relay_eligible": bool(policy["paper_policy_spec"]["relay_eligible"]),
        "chase_schedule": order.get("research_chase_schedule") or signal.get("research_chase_schedule") or {},
        "chase_schedule_authoritative": bool(order.get("chase_schedule_authoritative") or signal.get("chase_schedule_authoritative")),
        "entry_children": entry_children,
        "entry_children_count": len(entry_children),
        "atr14_pct_at_signal": atr14_pct_at_signal,
        "atr14_pct_basis": "SIGNAL_TIME_3M_ATR14" if atr14_pct_at_signal is not None else "UNAVAILABLE",
        **causal_ids,
        **policy,
        "effective_execution_mode": "PAPER_OBSERVED",
    })
    lifecycle = store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:paper-order-submitted", "episode_id": identity["episode_id"], "event_id": event_id,
        "shared_ai_call_id": identity["shared_ai_call_id"],
        "observation_status": "PAPER_ORDER_SUBMITTED",
        "outcome_state": normalize_lifecycle_outcome("PENDING_FILL"),
        "effective_execution_mode": "PAPER_OBSERVED",
        "terminal": False,
        "ranking_eligible": False, "ranking_blocker": "PATH_NOT_MATURED",
        "research_lane": _first(order.get("research_lane"), signal.get("research_lane")),
        **causal_ids,
        **policy,
    })
    resolution = None
    resolution_lane = str(_first(order.get("research_lane"), signal.get("research_lane")) or "")
    if resolution_lane:
        resolution = dual_write_lane_entry_resolution(
            {**dict(signal), **dict(order), "shared_ai_call_id": identity["shared_ai_call_id"]},
            lane=resolution_lane, entry_resolution="ORDER_SUBMITTED",
            exact_reason="ACTUAL_PAPER_LIMIT_SUBMITTED",
            epoch_id=str(epoch_id), data_dir=data_dir,
            observed_ts=float(_first(order.get("created_ts"), time.time())),
        )
    writes = [opportunity, intent, lifecycle]
    if resolution is not None:
        writes.append(resolution["write"])
    return {"schema": "v3_paper_order_intent_receipt_v1", "epoch_id": str(epoch_id), **identity,
            **causal_ids, **policy,
            "writes": writes, "store_verification": store.verify()}


def dual_write_paper_fill(order: Mapping[str, Any], signal: Mapping[str, Any], position: Mapping[str, Any], *, epoch_id: str, data_dir: str) -> dict[str, Any]:
    """Write an observed paper fill once, without claiming exchange execution."""
    event_id = str(_first(position.get("trade_id"), order.get("trade_id"), signal.get("trade_id")) or "")
    identity = _causal_identity(event_id, signal, order, position)
    # The lane-owned position/order is authoritative.  ``signal`` is shared
    # across sibling lanes and may carry only the base/control identity.
    policy = _paper_policy_identity(str(epoch_id), position, order, signal)
    schedule = order.get("research_chase_schedule") or signal.get("research_chase_schedule")
    causal_ids = _explicit_causal_ids(
        epoch_id=str(epoch_id), event_id=event_id, episode_id=identity["episode_id"],
        include_schedule=isinstance(schedule, Mapping) and schedule.get("authoritative") is True,
        include_fill=True,
    )
    lifecycle_identity = {
        "shared_ai_call_id": identity["shared_ai_call_id"],
        "research_lane": policy["paper_policy_spec"].get("research_lane"),
        **causal_ids,
        **policy,
    }
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    atr14_pct_at_fill = _paper_atr14_pct_3m(position, order)
    execution_receipt = _paper_fill_execution_receipt(order, position, signal)
    execution = store.append("execution", {
        "record_id": f"execution:{event_id}:primary-fill", "episode_id": identity["episode_id"], "event_id": event_id,
        "execution_world": "SHOWCASE_PAPER_OBSERVED", "fill_ts": _first(position.get("entry_ts"), order.get("fill_ts")),
        "fill_price": _first(position.get("entry"), order.get("fill_price")),
        "fill_model": _first(position.get("fill_model"), order.get("fill_model")),
        "atr14_pct_at_fill": atr14_pct_at_fill,
        "atr14_pct_basis": "FILL_TIME_3M_ATR14" if atr14_pct_at_fill is not None else "UNAVAILABLE",
        "authenticated_exchange_actual": False, "paper_observation": True,
        "source_market_evidence_required_for_conservative_claim": True,
        **execution_receipt,
        **lifecycle_identity,
    })
    lifecycle = store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:paper-filled", "episode_id": identity["episode_id"], "event_id": event_id,
        "observation_status": "PAPER_POSITION_OPEN", "outcome_state": "PARTIAL_FILL" if order.get("partial_fill") else "FULL_FILL",
        "effective_execution_mode": "PAPER_OBSERVED",
        "terminal": False, "ranking_eligible": False, "ranking_blocker": "EXIT_PATH_NOT_MATURED",
        **lifecycle_identity,
    })
    return {"schema": "v3_paper_fill_receipt_v1", "epoch_id": str(epoch_id), **identity,
            **causal_ids, **policy,
            "writes": [execution, lifecycle], "store_verification": store.verify()}


def dual_write_paper_close(position: Mapping[str, Any], signal: Mapping[str, Any], outcome: Mapping[str, Any], *, epoch_id: str, data_dir: str) -> dict[str, Any]:
    """Write the observed terminal paper result while replay paths continue."""
    event_id = str(_first(position.get("trade_id"), signal.get("trade_id"), outcome.get("trade_id")) or "")
    identity = _causal_identity(event_id, signal, position, outcome)
    # Preserve the identity frozen on the lane-owned position.  A shared AI
    # signal must never override it during terminal attribution.
    policy = _paper_policy_identity(str(epoch_id), position, outcome, signal)
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    schedule = (
        position.get("research_chase_schedule")
        or signal.get("research_chase_schedule")
        or outcome.get("research_chase_schedule")
    )
    schedule_available = isinstance(schedule, Mapping) and schedule.get("authoritative") is True
    start_ts = _timestamp(_first(
        signal.get("created_ts_ts"), signal.get("signal_ts"),
        position.get("signal_created_ts"), position.get("entry_ts"),
    ))
    close_ts = _timestamp(_first(outcome.get("close_ts"), outcome.get("ts")))
    segment_refs = []
    segment_writes = []
    segment_rows = []
    segment_coverage = {
        "schema": "paper_market_segment_coverage_v1",
        "row_count": 0,
        "two_second_or_better": False,
        "reason": "PATH_TIME_BOUNDS_MISSING",
    }
    if start_ts is not None and close_ts is not None and close_ts >= start_ts:
        segment_rows, segment_coverage = _paper_market_segment(
            data_dir, start_ts=start_ts, end_ts=close_ts,
        )
        if segment_rows:
            segment_ref = store.put_market_segment(
                source="LIVE_MICROSTRUCTURE_1S",
                symbol=identity["symbol"],
                timeframe="1s",
                start_ts=start_ts,
                end_ts=close_ts,
                rows=segment_rows,
            )
            segment_refs.append(segment_ref)
            tape_id = f"tape:{segment_ref['sha256']}"
            causal_ids = _explicit_causal_ids(
                epoch_id=str(epoch_id), event_id=event_id,
                episode_id=identity["episode_id"], include_schedule=schedule_available,
                include_fill=True, tape_id=tape_id,
            )
            lifecycle_identity = {
                "shared_ai_call_id": identity["shared_ai_call_id"],
                "research_lane": policy["paper_policy_spec"].get("research_lane"),
                **causal_ids, **policy,
            }
            segment_writes.append(store.append("market_segment", {
                "record_id": f"market-segment:{event_id}:{segment_ref['sha256']}",
                "episode_id": identity["episode_id"],
                "event_id": event_id,
                "segment_ref": segment_ref,
                "coverage": segment_coverage,
                **lifecycle_identity,
            }))
    if not segment_refs:
        causal_ids = _explicit_causal_ids(
            epoch_id=str(epoch_id), event_id=event_id,
            episode_id=identity["episode_id"], include_schedule=schedule_available,
            include_fill=True,
        )
        lifecycle_identity = {
            "shared_ai_call_id": identity["shared_ai_call_id"],
            "research_lane": policy["paper_policy_spec"].get("research_lane"),
            **causal_ids, **policy,
        }
    entry_price = _first(outcome.get("entry"), position.get("entry"))
    fill_ts = _first(position.get("entry_ts"), outcome.get("entry_ts"))
    path_receipt = _paper_path_receipt(
        segment_rows, direction=identity["executed_direction"],
        entry_price=entry_price, fill_ts=fill_ts,
    )
    execution = store.append("execution", {
        "record_id": f"execution:{event_id}:paper-close", "episode_id": identity["episode_id"], "event_id": event_id,
        "execution_world": "SHOWCASE_PAPER_OBSERVED", "close_ts": _first(outcome.get("close_ts"), outcome.get("ts")),
        "entry_price": entry_price, "exit_price": outcome.get("exit"),
        "filled_qty": _first(outcome.get("execution_qty"), position.get("qty")), "net_pnl_usd": outcome.get("net_pnl_usd"),
        "gross_pnl_usd": outcome.get("gross_pnl_usd"), "trading_fees_usd": outcome.get("trading_fees_usd"),
        "funding_fees_usd": outcome.get("funding_fees_usd"), "exit_reason": outcome.get("exit_reason"),
        "entry_context": _observed_context(outcome, phase="ENTRY"),
        "exit_context": _observed_context(outcome, phase="EXIT"),
        "path_extrema": path_receipt,
        "protection_trajectory": {
            "basis": "TERMINAL_STATE_PLUS_PARTIAL_RECEIPTS",
            "exit_config": copy.deepcopy(_first(position.get("exit_config"), signal.get("exit_config"))),
            "initial_stop_price": _first(position.get("initial_sl"), position.get("sl_at_entry")),
            "terminal_stop_price": position.get("sl"),
            "terminal_target_price": position.get("tp"),
            "terminal_trailing_stop_price": _first(position.get("trailing_stop"), position.get("trail_stop")),
            "terminal_peak_margin_pct": _first(outcome.get("max_profit"), position.get("max_pnl_pct")),
            "terminal_mae_margin_pct": _first(outcome.get("max_drawdown"), position.get("max_drawdown")),
            "terminal_tp_stage": _first(outcome.get("tp_stage"), position.get("tp_stage")),
            "terminal_remaining_fraction": _first(outcome.get("policy_remaining_fraction"), position.get("policy_remaining_fraction")),
            "partial_exit_count": len(outcome.get("partial_exit_receipts") or []),
            "terminal_exit_reason": outcome.get("exit_reason"),
        },
        "partial_exits": _normalized_partial_exits(outcome),
        "exit_market_receipt": copy.deepcopy(
            outcome.get("exit_market_receipt")
            if isinstance(outcome.get("exit_market_receipt"), Mapping)
            else {"basis": "UNAVAILABLE"}
        ),
        "authenticated_exchange_actual": False, "paper_observation": True, **lifecycle_identity,
    })
    lifecycle = store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:paper-closed", "episode_id": identity["episode_id"], "event_id": event_id,
        "observation_status": "PAPER_POSITION_CLOSED",
        "outcome_state": normalize_lifecycle_outcome(
            "PAPER_REALIZED", net_pnl_usd=outcome.get("net_pnl_usd")
        ),
        "effective_execution_mode": "PAPER_OBSERVED",
        "terminal": True,
        "ranking_eligible": False,
        "ranking_blocker": (
            "POLICY_REPLAY_PENDING" if segment_refs and segment_coverage.get("two_second_or_better")
            else "MARKET_PATH_INCOMPLETE"
        ),
        "market_segment_refs": segment_refs,
        "market_segment_coverage": segment_coverage,
        "net_pnl_usd": outcome.get("net_pnl_usd"), "exit_reason": outcome.get("exit_reason"),
        **lifecycle_identity,
    })
    return {"schema": "v3_paper_close_receipt_v1", "epoch_id": str(epoch_id), **identity,
            **causal_ids, **policy,
            "writes": [execution, *segment_writes, lifecycle], "store_verification": store.verify()}


def dual_write_provisional_source(event_id: str, source: Mapping[str, Any], *, epoch_id: str, data_dir: str) -> dict[str, Any]:
    """Record the causal opportunity immediately, before its long path matures."""
    signal_ts = float(_first(source.get("created_ts_ts"), source.get("signal_ts"), 0) or 0)
    direction = str(_first(source.get("raw_direction"), source.get("final_direction"), "UNKNOWN")).upper()
    symbol = str(_first(source.get("symbol"), source.get("pair"), "BTCUSD")).upper()
    shared = str(source.get("shared_ai_call_id") or "").strip()
    stable_episode_id = str(source.get("event_episode_id") or "").strip()
    if not shared and not stable_episode_id:
        # Do not mint a fallback episode while the signal is still being
        # enriched.  A later upsert commonly supplies the shared AI identity;
        # writing now would leave an immutable orphan and inflate N.
        return {
            "schema": "v3_provisional_dual_write_receipt_v1",
            "event_id": str(event_id),
            "written": False,
            "deferred": True,
            "reason": "CAUSAL_IDENTITY_PENDING",
            "writes": [],
        }
    if shared:
        causal_key = f"shared:{shared}"
        grouping_basis = "SHARED_AI_CALL"
        episode_id = "episode-" + hashlib.sha256(causal_key.encode("utf-8")).hexdigest()[:20]
    else:
        episode_id = stable_episode_id
        grouping_basis = "STABLE_EVENT_EPISODE"
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    opportunity = store.append("opportunity", {
        "record_id": f"opportunity:{episode_id}",
        "episode_id": episode_id,
        "shared_ai_call_id": shared or None,
        "signal_ts": signal_ts,
        "symbol": symbol,
        "raw_direction": direction,
        "feature_snapshot_at_signal": source.get("research_feature_snapshot") or {},
        "first_observed_as_provisional": True,
        "grouping_basis": grouping_basis,
        "collector_version": COLLECTOR_VERSION,
    })
    lifecycle = store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:opened",
        "episode_id": episode_id,
        "event_id": str(event_id),
        "observation_status": str(source.get("observation_status") or "PENDING"),
        "outcome_state": "CENSORED",
        "terminal": False,
        "ranking_eligible": False,
        "ranking_blocker": "PATH_NOT_MATURED",
    })
    return {
        "schema": "v3_provisional_dual_write_receipt_v1",
        "event_id": str(event_id),
        "episode_id": episode_id,
        "writes": [opportunity, lifecycle],
        "store_verification": store.verify(),
    }


def dual_write_v22_record(record: Mapping[str, Any], *, data_dir: str) -> dict[str, Any]:
    """Normalize one durable v2.2 event without copying its market path per row."""
    envelope = record.get("envelope") if isinstance(record.get("envelope"), Mapping) else {}
    epoch_id = str(_first(record.get("epoch_id"), envelope.get("epoch_id")) or "")
    event_id = str(_first(record.get("event_id"), record.get("trade_id")) or "")
    event_episode = record.get("event_episode") if isinstance(record.get("event_episode"), Mapping) else {}
    feature_snapshot = record.get("feature_snapshot_at_signal") if isinstance(record.get("feature_snapshot_at_signal"), Mapping) else {}
    source_features = feature_snapshot.get("source_features") if isinstance(feature_snapshot.get("source_features"), Mapping) else {}
    shared_ai_call_id = str(_first(
        event_episode.get("shared_ai_call_id"), record.get("shared_ai_call_id"),
        source_features.get("shared_ai_call_id"), envelope.get("shared_ai_call_id"),
    ) or "").strip()
    policy_identity = record.get("policy_identity") if isinstance(record.get("policy_identity"), Mapping) else {}
    envelope_policy_identity = envelope.get("policy_identity") if isinstance(envelope.get("policy_identity"), Mapping) else {}
    policy_id = str(_first(
        record.get("base_policy_id"), record.get("policy_id"),
        envelope.get("base_policy_id"), envelope.get("policy_id"),
        policy_identity.get("base_policy_id"), envelope_policy_identity.get("base_policy_id"),
    ) or "").strip()
    policy_signature = str(_first(
        record.get("policy_signature"), envelope.get("policy_signature"),
        policy_identity.get("policy_signature"), envelope_policy_identity.get("policy_signature"),
    ) or "").strip()
    policy_epoch_id = str(_first(
        record.get("policy_epoch_id"), envelope.get("policy_epoch_id"),
        policy_identity.get("policy_epoch_id"), envelope_policy_identity.get("policy_epoch_id"),
    ) or "").strip()
    # The durable V2.2 writer did not always record a lane name.  Its explicit
    # base policy is still a truthful source-policy scope, but an event-episode
    # fallback is not a shared AI-call ID and must never be relabelled as one.
    research_lane = str(_first(
        record.get("research_lane"), envelope.get("research_lane"), policy_id,
    ) or "").strip()
    policy_provenance = {
        "policy_id": policy_id or None,
        "policy_signature": policy_signature or None,
        "policy_epoch_id": policy_epoch_id or None,
        "research_lane": research_lane or None,
        "shared_ai_call_id": shared_ai_call_id or None,
    }
    complete_execution_identity = all(policy_provenance.values())
    stable_episode_id = str(_first(record.get("event_episode_id"), envelope.get("event_episode_id")) or "")
    identity_symbol = str(_first(record.get("symbol"), record.get("pair"), envelope.get("symbol"), "BTCUSD")).upper()
    identity_direction = str(_first(envelope.get("raw_direction"), record.get("raw_direction"), record.get("direction"), "UNKNOWN")).upper()
    if shared_ai_call_id:
        causal_key = f"shared:{shared_ai_call_id}"
        episode_id = "episode-" + hashlib.sha256(causal_key.encode("utf-8")).hexdigest()[:20]
    else:
        episode_id = stable_episode_id
    if not epoch_id or not event_id or not episode_id:
        raise ValueError("V3_IDENTITY_INCOMPLETE")
    store = V3EvidenceStore(data_dir, epoch_id=epoch_id)
    tape = record.get("canonical_tape") if isinstance(record.get("canonical_tape"), Mapping) else {}
    path_1m = tape.get("path_1m") if isinstance(tape.get("path_1m"), list) else []
    ticks_1s = tape.get("ticks_1s_optional") if isinstance(tape.get("ticks_1s_optional"), list) else []
    signal_ts = float(_first(envelope.get("signal_ts"), record.get("signal_ts"), 0) or 0)
    symbol = str(_first((record.get("feature_snapshot_at_signal") or {}).get("symbol"), "BTCUSD"))
    segment_refs = []
    if path_1m:
        path_1m = _normalize_market_rows(path_1m, timeframe="1m")
        segment_refs.append(store.put_market_segment(
            source="CANONICAL_1M", symbol=symbol, timeframe="1m",
            start_ts=float(_first(tape.get("canonical_tape_start"), signal_ts) or signal_ts),
            end_ts=float(_first(tape.get("canonical_tape_end"), signal_ts) or signal_ts),
            rows=path_1m,
        ))
    if ticks_1s:
        ticks_1s = _normalize_market_rows(ticks_1s, timeframe="1s")
        times = [float(_first(row.get("t"), row.get("ts"), 0) or 0) for row in ticks_1s]
        segment_refs.append(store.put_market_segment(
            source="CANONICAL_1S", symbol=symbol, timeframe="1s",
            start_ts=min(times), end_ts=max(times), rows=ticks_1s,
        ))

    writes = []
    writes.append(store.append("opportunity", {
        "record_id": f"opportunity:{episode_id}",
        "episode_id": episode_id,
        "shared_ai_call_id": shared_ai_call_id or None,
        "signal_ts": signal_ts,
        "symbol": symbol,
        "raw_direction": _first(envelope.get("raw_direction"), record.get("raw_direction")),
        "feature_snapshot_at_signal": record.get("feature_snapshot_at_signal") or {},
        "pre_signal_context": record.get("pre_signal_context") or {},
        "collector_version": COLLECTOR_VERSION,
    }))
    writes.append(store.append("decision", {
        "record_id": f"decision:{event_id}",
        "episode_id": episode_id,
        "event_id": event_id,
        "executed_direction": _first(envelope.get("executed_direction"), record.get("direction")),
        "primary_outcome": _first(record.get("primary_outcome"), envelope.get("primary_outcome")),
        "decision_tree_snapshot": record.get("decision_tree_snapshot") or {},
        "exact_reason": record.get("exact_reason"),
        "would_block": record.get("would_block"),
        "would_block_reason": record.get("would_block_reason"),
        **policy_provenance,
    }))
    writes.append(store.append("order_intent", {
        "record_id": f"order-intent:{event_id}",
        "episode_id": episode_id,
        "event_id": event_id,
        "execution_basis": record.get("research_execution_basis") or envelope.get("research_execution_basis") or {},
        "chase_schedule": record.get("research_chase_schedule") or envelope.get("research_chase_schedule") or {},
        "entry_children_count": len(record.get("entry_children") or []),
        # These are immutable policy-intent results, not duplicated market
        # paths. Keeping the exact fill/chase receipts lets the V3 analyzer
        # evaluate protection variants without rereading mutable v2 rows.
        "entry_children": record.get("entry_children") or [],
        "signal_price": _first(envelope.get("signal_price"), record.get("signal_price")),
        "executed_direction": _first(envelope.get("executed_direction"), record.get("direction")),
        "atr14_pct": _first(record.get("atr14_pct"), envelope.get("atr14_pct")),
        "leverage": _first((record.get("research_execution_basis") or {}).get("leverage"), (envelope.get("control_cell") or {}).get("leverage"), 100.0),
        "margin_usd": _first((record.get("research_execution_basis") or {}).get("margin_usd"), (envelope.get("control_cell") or {}).get("margin_usd"), 0.25),
        "search_receipt": envelope.get("policy_search") or {},
        **policy_provenance,
    }))
    source_fill_present = record.get("live_fill_ts") is not None or record.get("live_fill_price") is not None
    if source_fill_present and complete_execution_identity:
        writes.append(store.append("execution", {
            "record_id": f"execution:{event_id}:primary-fill",
            "episode_id": episode_id,
            "event_id": event_id,
            "execution_world": "PAPER_OR_SOURCE_RECORDED",
            "fill_ts": record.get("live_fill_ts"),
            "fill_price": record.get("live_fill_price"),
            "quantity_basis": record.get("research_execution_basis") or {},
            "authenticated_exchange_actual": False,
            **policy_provenance,
        }))
    for ref in segment_refs:
        writes.append(store.append("market_segment", {
            "record_id": f"market-segment:{event_id}:{ref['sha256']}",
            "episode_id": episode_id,
            "event_id": event_id,
            "segment_ref": ref,
            "coverage": tape.get("coverage") or {},
        }))
    ranking_eligible = bool(record.get("ranking_eligible")) and (
        not source_fill_present or complete_execution_identity
    )
    writes.append(store.append("lifecycle", {
        "record_id": f"lifecycle:{event_id}:terminal",
        "episode_id": episode_id,
        "event_id": event_id,
        "observation_status": record.get("observation_status"),
        "terminal_reason": _first(
            record.get("exact_reason"), record.get("terminal_provenance"),
            record.get("primary_outcome"),
        ),
        "terminal_no_fill": record.get("primary_outcome") == "ACCEPTED_UNFILLED",
        "terminal_ttl_expired": "TTL_EXPIRED" in str(_first(
            record.get("exact_reason"), record.get("terminal_provenance"), "",
        )).upper(),
        "fill_time_revalidation": copy.deepcopy(
            record.get("fill_time_revalidation")
            if isinstance(record.get("fill_time_revalidation"), Mapping)
            else {"performed": False, "result": "UNAVAILABLE"}
        ),
        "terminal": True,
        "outcome_state": (
            "DATA_ERROR" if record.get("observation_status") == "DATA_ERROR"
            else "CENSORED" if record.get("negative_evidence") is True
            else "REJECTED" if record.get("primary_outcome") == "REJECTED"
            else "FULL_FILL" if record.get("primary_outcome") == "ACCEPTED_FILLED"
            else "NO_FILL" if record.get("primary_outcome") == "ACCEPTED_UNFILLED"
            else "UNSUPPORTED"
        ),
        "ranking_eligible": ranking_eligible,
        "ranking_blocker": (
            None if ranking_eligible
            else "SOURCE_FILL_CAUSAL_IDENTITY_INCOMPLETE" if source_fill_present and not complete_execution_identity
            else "SOURCE_NOT_RANKING_ELIGIBLE"
        ),
        "replay_eligibility": record.get("replay_eligibility") or {},
        "market_segment_refs": segment_refs,
        **policy_provenance,
    }))
    return {
        "schema": "v22_to_v3_dual_write_receipt_v1",
        "epoch_id": epoch_id,
        "event_id": event_id,
        "episode_id": episode_id,
        "writes": writes,
        "source_fill_recorded": source_fill_present,
        "execution_normalized": bool(source_fill_present and complete_execution_identity),
        "execution_normalization_blocker": (
            None if not source_fill_present or complete_execution_identity
            else "SOURCE_FILL_CAUSAL_IDENTITY_INCOMPLETE"
        ),
        "store_verification": store.verify(),
    }


def reconcile_terminal_v22_into_v3(
    *,
    data_dir: str,
    epoch_id: str,
    events_file: str = "research_events_v22.jsonl",
) -> dict[str, Any]:
    """Backfill terminal V2 rows missed during a V3 bridge rollout or crash.

    The durable V2 ledger remains authoritative during migration.  V3 writes
    are idempotent by record_id, and only exact-current-epoch terminal rows are
    eligible for repair.  Corrupt/truncated source or V3 ledgers fail closed.
    """
    root = Path(data_dir)
    source = root / events_file
    store = V3EvidenceStore(root, epoch_id=str(epoch_id))
    lifecycle_path = store.ledger_path("lifecycle")
    durable_lifecycle_ids = V3EvidenceStore._load_ids(lifecycle_path)
    terminal_statuses = {"COMPLETE", "FUNNEL_COMPLETE", "DATA_ERROR", "INSUFFICIENT_PATH"}
    scanned = current_epoch_terminal = backfilled = already_present = foreign_epoch = 0
    errors: list[dict[str, Any]] = []
    if not source.exists():
        return {
            "schema": "v3_terminal_reconciliation_v1",
            "epoch_id": str(epoch_id),
            "source": events_file,
            "scanned": 0,
            "current_epoch_terminal": 0,
            "backfilled": 0,
            "already_present": 0,
            "foreign_epoch": 0,
            "errors": [],
            "passed": True,
            "store_verification": store.verify(),
        }
    with source.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.endswith("\n"):
                raise ValueError(f"TRUNCATED_V22_JSONL_LINE:{line_no}")
            scanned += 1
            record = json.loads(line)
            envelope = record.get("envelope") if isinstance(record.get("envelope"), Mapping) else {}
            row_epoch = str(_first(record.get("epoch_id"), envelope.get("epoch_id")) or "")
            if row_epoch != str(epoch_id):
                foreign_epoch += 1
                continue
            status = str(record.get("observation_status") or "")
            if status not in terminal_statuses:
                continue
            current_epoch_terminal += 1
            event_id = str(_first(record.get("event_id"), record.get("trade_id")) or "")
            terminal_id = f"lifecycle:{event_id}:terminal"
            if not event_id:
                errors.append({"line": line_no, "reason": "MISSING_EVENT_ID"})
                continue
            if terminal_id in durable_lifecycle_ids:
                already_present += 1
                continue
            try:
                receipt = dual_write_v22_record(record, data_dir=str(root))
                verification = receipt.get("store_verification") or {}
                if not verification.get("passed"):
                    errors.append({"line": line_no, "event_id": event_id, "reason": "V3_STORE_VERIFICATION_FAILED"})
                    continue
                durable_lifecycle_ids.add(terminal_id)
                backfilled += 1
            except Exception as exc:
                errors.append({"line": line_no, "event_id": event_id, "reason": f"{type(exc).__name__}:{exc}"})
    verification = store.verify()
    return {
        "schema": "v3_terminal_reconciliation_v1",
        "epoch_id": str(epoch_id),
        "source": events_file,
        "scanned": scanned,
        "current_epoch_terminal": current_epoch_terminal,
        "backfilled": backfilled,
        "already_present": already_present,
        "foreign_epoch": foreign_epoch,
        "errors": errors,
        "passed": not errors and bool(verification.get("passed")),
        "store_verification": verification,
    }
