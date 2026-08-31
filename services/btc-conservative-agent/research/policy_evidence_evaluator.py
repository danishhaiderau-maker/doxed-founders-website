"""Generation-bound conservative policy evidence evaluation.

This is a read-only analyzer component.  It consumes exact V3 causal bindings,
content-addressed one-second market segments and authoritative order schedules.
It never supplies defaults for missing evidence and never changes an order.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from research.conservative_limit_fill import evaluate_limit_fill
from research.policy_evidence_bindings import (
    ALL_OPPORTUNITY_FUTURE_ROLE,
    authoritative_future_path_segments,
    authoritative_schedule_intents,
    build_v3_binding_index,
    complete_conservative_future_path,
    segment_role,
)
from research.policy_evidence_schema import canonical_json, generation_identity, stable_hash


SCHEMA = "v3_conservative_policy_evidence_v1"
EVIDENCE_WORLD = "CONSERVATIVE_BBO_DEPTH_TAPE"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"V3_LEDGER_ROW_NOT_OBJECT:{path.name}:{number}")
            rows.append(value)
    return rows


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(str(row.get(field) or "") for field in ("epoch_id", "opportunity_id", "episode_id"))


def _policy_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (*_identity(row), str(row.get("policy_signature") or ""))


def _number(*values: Any) -> float | None:
    for value in values:
        try:
            result = float(value)
        except (TypeError, ValueError):
            continue
        if result == result:
            return result
    return None


def _load_envelope(root: Path, segment: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    ref = segment.get("segment_ref") if isinstance(segment.get("segment_ref"), Mapping) else {}
    digest = str(ref.get("sha256") or "").lower()
    relative = str(ref.get("relative_path") or "")
    try:
        target = (root.parent / relative).resolve()
        target.relative_to(root.parent.resolve())
    except (ValueError, OSError):
        return None, "UNKNOWN_TAPE_PATH_OUTSIDE_V3"
    canonical = (root / "market_segments" / digest[:2] / f"{digest}.json").resolve()
    if target != canonical or len(digest) != 64:
        return None, "UNKNOWN_TAPE_PATH_NOT_CANONICAL"
    try:
        payload = target.read_bytes()
    except OSError:
        return None, "UNKNOWN_TAPE_OBJECT_MISSING"
    if hashlib.sha256(payload).hexdigest() != digest:
        return None, "UNKNOWN_TAPE_CHECKSUM_MISMATCH"
    try:
        envelope = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "UNKNOWN_TAPE_ENVELOPE_INVALID"
    if not isinstance(envelope, dict) or envelope.get("schema") != "market_segment_v3":
        return None, "UNKNOWN_TAPE_ENVELOPE_SCHEMA_INVALID"
    return envelope, None


def _schedule(intent: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    schedule = intent.get("chase_schedule")
    if not isinstance(schedule, Mapping) or schedule.get("authoritative") is not True:
        return None
    raw = schedule.get("intervals")
    if not isinstance(raw, list) or not raw:
        return None
    result = []
    for index, interval in enumerate(raw):
        if not isinstance(interval, Mapping):
            return None
        start = _number(interval.get("start_ts"), interval.get("start"))
        end = _number(interval.get("end_ts"), interval.get("end"))
        limit = _number(interval.get("limit_price"), interval.get("price"))
        if start is None or end is None or limit is None:
            return None
        result.append({
            "bucket_id": str(interval.get("bucket_id") or interval.get("stage_id") or f"stage-{index}"),
            "start_ts": start, "end_ts": end, "limit_price": limit,
            "generation": schedule.get("generation_id") or intent.get("schedule_id"),
        })
    return result


def _unknown(binding: Mapping[str, Any], decision: Mapping[str, Any],
             opportunity: Mapping[str, Any], reasons: list[str]) -> dict[str, Any]:
    tape_ids = sorted(binding.get("tape_ids") or [])
    identity_values = [
        binding.get("epoch_id"), binding.get("opportunity_id"),
        binding.get("episode_id"),
    ]
    cohort = (
        stable_hash("cohort", {
            "epoch_id": identity_values[0], "opportunity_id": identity_values[1],
            "episode_id": identity_values[2], "tape_ids": tape_ids,
        })
        if all(str(value or "") for value in identity_values)
        else None
    )
    policy_spec = (
        decision.get("paper_policy_spec")
        if isinstance(decision.get("paper_policy_spec"), Mapping) else {}
    )
    exit_config = (
        policy_spec.get("exit_config")
        if isinstance(policy_spec.get("exit_config"), Mapping) else {}
    )
    feature_snapshot = (
        opportunity.get("feature_snapshot_at_signal")
        if isinstance(opportunity.get("feature_snapshot_at_signal"), Mapping) else {}
    )
    market_context = (
        feature_snapshot.get("market_context")
        if isinstance(feature_snapshot.get("market_context"), Mapping) else {}
    )
    entry_offset_pct = decision.get("entry_offset_pct")
    if entry_offset_pct is None and policy_spec.get("entry_offset_fraction") is not None:
        fraction = _number(policy_spec.get("entry_offset_fraction"))
        entry_offset_pct = fraction * 100.0 if fraction is not None else None
    ai_direction = str(opportunity.get("raw_direction") or "").upper() or None
    side_candidate = str(
        decision.get("direction") or decision.get("side")
        or decision.get("executed_direction") or ai_direction or ""
    ).upper() or None
    side = side_candidate if side_candidate in {"LONG", "SHORT"} else None
    return {
        "schema": SCHEMA,
        "epoch_id": binding.get("epoch_id"),
        "opportunity_id": binding.get("opportunity_id"),
        "episode_id": binding.get("episode_id"),
        "decision_id": binding.get("event_id"),
        "policy_id": binding.get("policy_id"),
        "policy_signature": binding.get("policy_signature"),
        "evidence_world": EVIDENCE_WORLD,
        "comparison_cohort_key": cohort,
        "lane": binding.get("research_lane"),
        "family": (
            decision.get("policy_family") or decision.get("family")
            or exit_config.get("family")
        ),
        "entry_offset_pct": entry_offset_pct,
        "chase_policy": (
            decision.get("chase_policy") or policy_spec.get("entry_limit_policy")
        ),
        "exit_family": (
            decision.get("exit_family") or exit_config.get("exit_profile_id")
            or exit_config.get("family")
        ),
        "regime": (
            decision.get("regime") or decision.get("market_regime")
            or market_context.get("regime_label")
        ),
        "side": side,
        "split": str(decision.get("split") or "").upper() or None,
        "ai_direction": ai_direction,
        "ai_decision": str(decision.get("raw_ai_decision") or "").upper() or None,
        "long_score": decision.get("long_score"),
        "short_score": decision.get("short_score"),
        "score_gap": decision.get("score_gap"),
        "classification": "UNKNOWN", "supported": False,
        "unknown_reason_codes": sorted(set(reasons)),
        "requested_qty": None, "available_qty": None, "raw_partial_qty": None,
        "rounded_executable_qty": None, "accumulated_qty": None, "filled_qty": None,
        "minimum_lot_decision": "UNKNOWN", "minimum_notional_decision": "UNKNOWN",
        "quantity_attempts": [], "gross_pnl_usd": None, "fees_usd": None,
        "slippage_usd": None, "fill_latency_sec": None,
        "price_concession_per_unit": None,
        "missed_entry_cost_usd": None,
        "missed_entry_cost_basis": "UNAVAILABLE_REQUIRES_DECLARED_MARK_HORIZON",
        "volatility_at_signal": {
            # Preserve the collector's native normalized fields individually.
            # Collapsing these to a single generic value made ATR/percentile
            # stratification impossible even when the opportunity retained
            # both measurements.
            "volatility_atr": feature_snapshot.get("volatility_atr"),
            "volatility_percentile": feature_snapshot.get("volatility_percentile"),
            "atr14": feature_snapshot.get("atr14"),
            "atr14_pct_3m": feature_snapshot.get("atr14_pct_3m"),
            "realized_volatility": feature_snapshot.get("realized_volatility"),
            "volatility_metric": (
                feature_snapshot.get("volatility_metric")
                or market_context.get("volatility_metric")
            ),
        },
        "net_pnl_usd": None,
    }


def build_v3_conservative_results(v3_root: str | Path) -> dict[str, Any]:
    """Evaluate exactly-bound decisions and retain every UNKNOWN explicitly."""
    root = Path(v3_root).resolve()
    if root.name != "v3":
        raise ValueError("V3_EVALUATOR_ROOT_MUST_BE_V3")
    ledgers = {
        name: _read_jsonl(root / "ledgers" / f"{name}.jsonl")
        for name in ("decision", "opportunity", "order_intent", "market_segment")
    }
    recovery_segments = _read_jsonl(
        root / "recovery_ledgers" / "market_segment.jsonl"
    )
    ledgers["market_segment"].extend(recovery_segments)
    bindings = build_v3_binding_index(root)["bindings"]
    decisions = {str(row.get("event_id") or ""): row for row in ledgers["decision"]}
    opportunities = {_identity(row): row for row in ledgers["opportunity"]}
    intents: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    segments: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    envelope_cache: dict[str, tuple[dict[str, Any] | None, str | None]] = {}

    def load_envelope_cached(segment: Mapping[str, Any]):
        ref = segment.get("segment_ref") if isinstance(segment.get("segment_ref"), Mapping) else {}
        key = canonical_json(ref)
        if key not in envelope_cache:
            envelope_cache[key] = _load_envelope(root, segment)
        return envelope_cache[key]

    def verify_segment_cached(segment: Mapping[str, Any]):
        _envelope, error = load_envelope_cached(segment)
        if error:
            return None, [error]
        ref = segment.get("segment_ref") if isinstance(segment.get("segment_ref"), Mapping) else {}
        return str(ref.get("sha256") or "") or None, []
    for row in ledgers["order_intent"]:
        intents.setdefault(_policy_identity(row), []).append(row)
    for row in ledgers["market_segment"]:
        segments.setdefault(_identity(row), []).append(row)

    results = []
    for binding in bindings:
        decision = decisions.get(str(binding.get("event_id") or ""), {})
        identity = _identity(binding)
        reasons = list(binding.get("unknown_reason_codes") or [])
        matching_intents = authoritative_schedule_intents(
            intents.get((*identity, str(binding.get("policy_signature") or "")), [])
        )
        schedule_fingerprints = {
            (str(row.get("schedule_id") or ""), str(row.get("schedule_sha256") or ""))
            for row in matching_intents
        }
        if len(schedule_fingerprints) != 1:
            reasons.append("UNKNOWN_AUTHORITATIVE_INTENT_NOT_UNIQUE")
        intent = matching_intents[-1] if len(schedule_fingerprints) == 1 else {}
        schedule = _schedule(intent)
        if schedule is None:
            reasons.append("UNKNOWN_AUTHORITATIVE_SCHEDULE_UNUSABLE")
        direction = str(
            decision.get("direction") or decision.get("side")
            or opportunities.get(identity, {}).get("raw_direction") or ""
        ).upper()
        requested_qty = _number(intent.get("requested_qty"), intent.get("quantity"))
        constraints = intent.get("signed_quantity_constraints")
        if direction not in {"LONG", "SHORT"}:
            reasons.append("UNKNOWN_DIRECTION_MISSING_OR_INVALID")
        if requested_qty is None or requested_qty <= 0:
            reasons.append("UNKNOWN_REQUESTED_QUANTITY_MISSING")

        tape_rows: list[dict[str, Any]] = []
        tape_ids: list[str] = []
        selected_segments, _future_history = authoritative_future_path_segments(
            root, segments.get(identity, []), verifier=verify_segment_cached,
        )
        for segment in selected_segments:
            coverage = segment.get("coverage") if isinstance(segment.get("coverage"), Mapping) else {}
            role = segment_role(segment)
            if role not in {
                "ENTRY_PATH", "ENTRY_AND_EXIT_PATH", "FULL_LIFECYCLE",
                ALL_OPPORTUNITY_FUTURE_ROLE,
            }:
                continue
            if role == ALL_OPPORTUNITY_FUTURE_ROLE:
                if not complete_conservative_future_path(segment):
                    reasons.append("UNKNOWN_FUTURE_ENTRY_PATH_INCOMPLETE")
                    continue
            elif coverage.get("conservative_bbo_depth_eligible") is not True:
                reasons.append("UNKNOWN_ENTRY_SEGMENT_NOT_CONSERVATIVE_ELIGIBLE")
                continue
            envelope, error = load_envelope_cached(segment)
            if error:
                reasons.append(error)
                continue
            rows = envelope.get("rows") if isinstance(envelope.get("rows"), list) else []
            if not rows:
                reasons.append("UNKNOWN_ENTRY_SEGMENT_ROWS_MISSING")
                continue
            tape_rows.extend(row for row in rows if isinstance(row, dict))
            tape_ids.append(str(segment["segment_ref"]["sha256"]))

        if reasons:
            results.append(_unknown(binding, decision, opportunities.get(identity, {}), reasons))
            continue
        receipt = evaluate_limit_fill(
            tape_rows, direction=direction, requested_qty=requested_qty,
            chase_schedule=schedule, aggressor_window_sec=1,
            symbol=str(intent.get("symbol") or "BTCUSD"), quantity_constraints=constraints,
        )
        if receipt.get("supported") is not True:
            results.append(_unknown(binding, decision, opportunities.get(identity, {}), [
                "UNKNOWN_CONSERVATIVE_EVALUATOR_" + str(reason)
                for reason in receipt.get("negative_reasons") or ["UNSPECIFIED"]
            ]))
            continue
        classification = str(receipt.get("final_classification") or "UNKNOWN")
        if classification not in {"FULL_FILL", "PARTIAL_FILL", "NO_FILL"}:
            results.append(_unknown(binding, decision, opportunities.get(identity, {}), ["UNKNOWN_EVALUATOR_CLASSIFICATION"]))
            continue
        cohort = stable_hash("cohort", {
            "epoch_id": identity[0], "opportunity_id": identity[1],
            "episode_id": identity[2], "tape_ids": sorted(tape_ids),
        })
        row = _unknown(binding, decision, opportunities.get(identity, {}), [])
        row.update({
            "comparison_cohort_key": cohort, "classification": classification,
            "supported": True, "unknown_reason_codes": [],
            "requested_qty": receipt.get("requested_qty"),
            "available_qty": receipt.get("visible_executable_qty"),
            "raw_partial_qty": receipt.get("raw_partial_qty"),
            "rounded_executable_qty": receipt.get("rounded_executable_qty"),
            "accumulated_qty": receipt.get("accumulated_qty"),
            "filled_qty": receipt.get("filled_qty"),
            "minimum_lot_decision": receipt.get("minimum_lot_decision"),
            "minimum_notional_decision": receipt.get("minimum_notional_decision"),
            "quantity_attempts": receipt.get("quantity_attempts") or [],
            # Preserve the content identity of the authoritative persisted
            # schedule envelope. The fill evaluator also hashes its normalized
            # interval list; expose that separately rather than replacing the
            # signed collector identity with a derived representation.
            "schedule_sha256": binding.get("schedule_sha256"),
            "evaluated_schedule_sha256": receipt.get("schedule_sha256"),
            "tape_ids": sorted(tape_ids),
            "fill_price": receipt.get("fill_price"), "evaluator_receipt": receipt,
            "fill_latency_sec": receipt.get("fill_latency_sec"),
            "price_concession_per_unit": receipt.get("price_concession_per_unit"),
            "slippage_usd": receipt.get("slippage_usd"),
            "missed_entry_cost_usd": receipt.get("missed_entry_cost_usd"),
            "missed_entry_cost_basis": receipt.get("missed_entry_cost_basis"),
        })
        results.append(row)
    results.sort(key=lambda row: tuple(str(row.get(field) or "") for field in (
        "opportunity_id", "episode_id", "decision_id", "policy_signature"
    )))
    counts = {name: sum(row["classification"] == name for row in results)
              for name in ("FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN")}
    return {"schema": SCHEMA, "row_count": len(results), "classification_counts": counts,
            "results_sha256": hashlib.sha256(canonical_json(results).encode()).hexdigest(),
            "results": results}


def persist_v3_conservative_results(canonical_root: str | Path, *, analyzer_revision: str) -> dict[str, Any]:
    root = Path(canonical_root).resolve()
    if root.name != "canonical-research-data":
        raise ValueError("POLICY_EVALUATOR_ROOT_NOT_CANONICAL")
    manifest = json.loads((root / "canonical_dataset_current.json").read_text(encoding="utf-8-sig"))
    generation = generation_identity(manifest, analyzer_revision=analyzer_revision)
    report = build_v3_conservative_results(root / "v3")
    # Populate the disposable generation cache from these evaluator-produced
    # rows. UNKNOWN rows are retained so coverage failures are queryable and
    # cannot silently disappear from fill-rate denominators.
    from research.policy_evidence_library import PolicyEvidenceLibrary
    library = PolicyEvidenceLibrary(str(root), manifest, analyzer_revision=analyzer_revision)
    cache_rows = []
    cache_skip_reason_counts: dict[str, int] = {}
    for row in report["results"]:
        missing = [
            field for field in (
                "epoch_id", "opportunity_id", "episode_id", "decision_id", "policy_signature",
                "comparison_cohort_key",
            )
            if not str(row.get(field) or "")
        ]
        if missing:
            # The exhaustive artifact retains the original UNKNOWN row. The
            # relational query cache cannot safely key a row whose causal
            # identity is absent; skipping it is explicit and never fabricates
            # an opportunity ID from an episode or event ID.
            for field in missing:
                key = f"RESULT_IDENTITY_MISSING_{field.upper()}"
                cache_skip_reason_counts[key] = cache_skip_reason_counts.get(key, 0) + 1
            continue
        cache_rows.append(row)
    ingested = library.ingest(cache_rows)
    directory = root / "derived" / "policy-evidence" / generation["generation_key"]
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "conservative-results.jsonl.gz"
    fd, name = tempfile.mkstemp(prefix=".conservative-results.", suffix=".tmp", dir=directory)
    os.close(fd)
    temporary = Path(name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="conservative-results.jsonl", mode="wb", fileobj=raw, mtime=0) as zipped:
                for row in report["results"]:
                    zipped.write((canonical_json(row) + "\n").encode())
            raw.flush(); os.fsync(raw.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {key: value for key, value in report.items() if key != "results"} | {
        "generation": generation,
        "relative_path": destination.relative_to(root).as_posix(),
        "artifact_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "artifact_size_bytes": destination.stat().st_size,
        "cache_rows_ingested": ingested,
        "cache_rows_skipped_missing_identity": len(report["results"]) - len(cache_rows),
        "cache_skip_reason_counts": dict(sorted(cache_skip_reason_counts.items())),
    }
