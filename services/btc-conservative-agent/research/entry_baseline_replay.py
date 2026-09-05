"""Identical-opportunity conservative replay for signed entry baselines.

This module is research-only.  It accepts already-bound episode evidence and
never derives a fill from a candle, signal price, or ideal touch.  Every
baseline is evaluated by the same quantity-aware public-tape evaluator.  A
missing or mismatched identity, schedule, BBO/depth row, quantity constraint,
latency, or fee input produces UNKNOWN rather than NO_FILL.
"""
from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

try:
    from .conservative_limit_fill import evaluate_limit_fill
except ImportError:  # direct script/test execution
    from conservative_limit_fill import evaluate_limit_fill

from research_entry_baselines import (
    ENTRY_BASELINE_REGISTRY,
    missing_baseline_evidence,
)
from research_v3_contract import canonical_hash
from research.policy_evidence_bindings import (
    ALL_OPPORTUNITY_FUTURE_ROLE,
    _verify_segment,
    authoritative_future_path_segments,
    complete_conservative_future_path,
    segment_role,
)
from pathlib import Path
import hashlib
import json

from research.baseline_execution_context import (
    build_baseline_execution_context, accepted_fill_position, IDENTITY_FIELDS, VerifiedLedgerRowIndex,
)
from research.policy_evidence_schema import canonical_json, generation_identity


REPLAY_SCHEMA = "entry_baseline_same_opportunity_replay_v1"
EPISODE_RECEIPT_SCHEMA = "entry_baseline_episode_receipt_v1"


def _context_source_pins(root: Path, generation: Mapping, manifest: Mapping | None):
    """Bind selected raw sources to the promoted whole-dataset checksum."""
    manifest_path = root / "canonical_dataset_current.json"
    state_path = root / ".fly-sync-state.json"
    try:
        if manifest_path.stat().st_size > 1024 * 1024 or state_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("BASELINE_CONTEXT_MANIFEST_SIZE_LIMIT")
        with manifest_path.open("rb") as handle:
            manifest_bytes = handle.read(1024 * 1024 + 1)
        if len(manifest_bytes) > 1024 * 1024:
            raise ValueError("BASELINE_CONTEXT_MANIFEST_SIZE_LIMIT")
        actual_manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
        unhashed_manifest = {key: value for key, value in actual_manifest.items() if key != "entry_hash"}
        if hashlib.sha256(canonical_json(unhashed_manifest).encode()).hexdigest() != actual_manifest.get("entry_hash"):
            raise ValueError("BASELINE_CONTEXT_MANIFEST_ENTRY_HASH_MISMATCH")
        if manifest is not None and actual_manifest != manifest:
            raise ValueError("BASELINE_CONTEXT_MANIFEST_CHANGED")
        expected = generation_identity(actual_manifest,
            analyzer_revision=generation.get("analyzer_revision"),
            evaluator_version=generation.get("evaluator_version"))
        if expected != generation:
            raise ValueError("BASELINE_CONTEXT_MANIFEST_GENERATION_MISMATCH")
        with state_path.open("rb") as handle:
            state_bytes = handle.read(32 * 1024 * 1024 + 1)
        if len(state_bytes) > 32 * 1024 * 1024:
            raise ValueError("BASELINE_CONTEXT_MANIFEST_SIZE_LIMIT")
        state = json.loads(state_bytes.decode("utf-8-sig"))
        if not isinstance(state, dict) or len(state) > 100_000:
            raise ValueError("BASELINE_CONTEXT_STATE_SHAPE_OR_LIMIT")
        normalized = {}
        for name, record in sorted(state.items()):
            relative = str(name).replace("\\", "/")
            if not isinstance(record, dict) or relative in normalized:
                raise ValueError("BASELINE_CONTEXT_STATE_INVALID_OR_DUPLICATE")
            candidate = root / relative
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ValueError("BASELINE_CONTEXT_STATE_PATH_UNSAFE")
            candidate.resolve().relative_to(root.resolve())
            normalized[relative] = record
        material = {"revision": generation["source_revision"], "epoch": generation["epoch_id"], "files": normalized}
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest != actual_manifest.get("dataset_checksum"):
            raise ValueError("BASELINE_CONTEXT_DATASET_CHECKSUM_MISMATCH")
        pins = {name: str(record.get("sha256") or "").lower() for name, record in normalized.items()}
        return pins, normalized, [], (manifest_bytes, state_bytes)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError) as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("BASELINE_CONTEXT_") else "BASELINE_CONTEXT_PINNED_MANIFEST_UNAVAILABLE"
        return {}, {}, [code], None


def _context_envelope(relative: str, raw: bytes, row: Mapping) -> dict:
    return {"source_id": relative, "raw_bytes": raw, "row": dict(row),
            "row_sha256": hashlib.sha256(canonical_json(row).encode()).hexdigest()}


def _context_ledger_sources(root: Path, pins: Mapping, records: Mapping, index: VerifiedLedgerRowIndex):
    """Read exact known ledgers once, never recursively search runtime data.

    The stage-zero producer already writes the first two paths. The optional
    context ledger is an explicit contract for collected baseline-specific
    authorizations/observations; its absence is reported, never synthesized.
    """
    statuses = []
    for relative in ("chase_offset_touch_grid.jsonl", "chase_offset_touch_grid.jsonl.1",
                     "v3/ledgers/baseline_execution_context.jsonl", "v3/ledgers/opportunity.jsonl",
                     "v3/ledgers/market_segment.jsonl", "v3/recovery_ledgers/market_segment.jsonl"):
        if relative not in pins:
            statuses.append({"source_id": relative, "status": "UNAVAILABLE", "reason": "SOURCE_NOT_IN_PINNED_DATASET"})
            continue
        try:
            count = index.add_source(root, relative, expected_sha=pins[relative],
                expected_size=records[relative].get("size"), stage_only=relative.startswith("chase_offset_touch_grid.jsonl"))
            statuses.append({"source_id": relative, "status": "VERIFIED", "sha256": pins[relative],
                "selected_rows": count, "verification_basis": "VERIFIED_FULL_LEDGER_SHA256_STREAM_V1"})
        except (OSError, ValueError, UnicodeError, TypeError, RecursionError) as exc:
            statuses.append({"source_id": relative, "status": "UNKNOWN", "reason": str(exc)})
    return statuses


def _execution_context(episode: Mapping, result: Mapping, generation: Mapping) -> dict:
    identity = {field: episode.get(field) for field in IDENTITY_FIELDS}
    identity["epoch_id"] = episode.get("epoch_id") or episode.get("dataset_epoch")
    identity.update(direction=episode.get("direction"), symbol=episode.get("symbol"),
                    baseline_id=result["baseline_id"], baseline_policy_signature=result["policy_signature"])
    sources = episode.get("_baseline_context_sources") or []
    stages = [item for item in sources if item["row"].get("schema") == "compressed_chase_shadow_v1"]
    sizing = [item for item in sources if item["row"].get("schema") == "baseline_sizing_authorization_v1"
              and item["row"].get("baseline_id") == result["baseline_id"]]
    try:
        completion_ts = accepted_fill_position(result["conservative_receipt"])["completion_ts"]
        atr = [item for item in sources if item["row"].get("schema") == "baseline_fill_atr_observation_v1"
               and not isinstance(item["row"].get("observed_ts"), bool)
               and Decimal(str(item["row"].get("observed_ts"))) == completion_ts]
    except (ValueError, InvalidOperation, TypeError, KeyError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else "BASELINE_CONTEXT_FILL_TIME_INPUT_INVALID"
        return {"status": "UNKNOWN", "context": None, "reason_codes": [code]}
    coverage = episode.get("_baseline_context_coverage") or []
    reasons = list(episode.get("_baseline_context_pin_reasons") or [])
    if episode.get("_baseline_context_opportunity") is None:
        reasons.append("BASELINE_CONTEXT_OPPORTUNITY_ROW_MEMBERSHIP_MISSING")
    for name, values in (("STAGE_ZERO", stages), ("SIZING_AUTHORIZATION", sizing), ("EXACT_FILL_ATR", atr), ("COVERAGE", coverage)):
        if not values:
            reasons.append("BASELINE_CONTEXT_" + name + "_MISSING")
        elif name != "STAGE_ZERO" and len(values) != 1:
            reasons.append("BASELINE_CONTEXT_" + name + "_AMBIGUOUS")
    if reasons:
        return {"status": "UNKNOWN", "context": None, "reason_codes": sorted(set(reasons))}
    return build_baseline_execution_context(generation=generation, identity=identity,
        entry_receipt=result["conservative_receipt"], pinned_sources=episode.get("_baseline_context_pins") or {},
        stage_zero_evidence=stages, sizing_authorization=sizing[0], atr_evidence=atr[0],
        coverage_evidence=coverage[0]["object"], coverage_binding=coverage[0].get("binding"),
        opportunity_binding=episode.get("_baseline_context_opportunity"))


def _baseline_rows() -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in ENTRY_BASELINE_REGISTRY["baselines"])


def _unknown(
    baseline: Mapping[str, Any], episode: Mapping[str, Any], *codes: str,
) -> dict[str, Any]:
    return {
        "baseline_id": baseline["baseline_id"],
        "policy_signature": baseline["policy_signature"],
        "episode_id": episode.get("episode_id"),
        "opportunity_id": episode.get("opportunity_id"),
        "outcome_state": "UNKNOWN",
        "supported": False,
        "rejection_codes": list(dict.fromkeys(str(code) for code in codes if code)),
        "conservative_receipt": None,
        "baseline_spec": dict(baseline),
        "schedule_provenance": None,
    }


def _schedule_envelope(
    baseline: Mapping[str, Any], episode: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, list[str]]:
    schedules = episode.get("baseline_schedules")
    if not isinstance(schedules, Mapping):
        return None, ["BASELINE_SCHEDULES_MISSING"]
    envelope = schedules.get(baseline["baseline_id"])
    if not isinstance(envelope, Mapping):
        return None, ["BASELINE_SCHEDULE_MISSING"]
    failures = []
    for key in ("episode_id", "opportunity_id", "policy_signature"):
        expected = baseline["policy_signature"] if key == "policy_signature" else episode.get(key)
        if not expected or str(envelope.get(key) or "") != str(expected):
            failures.append(f"BASELINE_SCHEDULE_{key.upper()}_MISMATCH")
    schedule = envelope.get("schedule")
    if not isinstance(schedule, list) or not schedule:
        failures.append("BASELINE_SCHEDULE_MISSING_OR_EMPTY")
    return envelope, failures


def _rows_cover_timestamp(rows: list[Mapping[str, Any]], timestamp: Any) -> bool:
    try:
        expected = int(float(timestamp))
    except (TypeError, ValueError):
        return False
    return any(
        row.get("schema") == "market_microstructure_1s_v1"
        and row.get("fresh") is True
        and row.get("valid_bbo") is True
        and int(float(row.get("bucket_ts"))) == expected
        for row in rows
        if row.get("bucket_ts") is not None
    )


def _market_schedule_failures(
    baseline: Mapping[str, Any], episode: Mapping[str, Any],
    envelope: Mapping[str, Any], rows: list[Mapping[str, Any]],
) -> list[str]:
    baseline_id = baseline["baseline_id"]
    if baseline_id not in {"MARKET_ENTRY_AT_SIGNAL", "FINAL_MARKET_AFTER_EXPIRY"}:
        return []
    timestamp = (
        episode.get("signal_ts") if baseline_id == "MARKET_ENTRY_AT_SIGNAL"
        else episode.get("expiry_ts")
    )
    try:
        expected_ts = int(float(timestamp))
    except (TypeError, ValueError):
        return ["MARKET_ENTRY_TIMESTAMP_MISSING"]
    schedule = envelope.get("schedule") or []
    if len(schedule) != 1:
        return ["MARKET_ENTRY_REQUIRES_ONE_SECOND_SCHEDULE"]
    interval = schedule[0]
    try:
        if int(interval.get("start_ts")) != expected_ts or int(interval.get("end_ts")) != expected_ts + 1:
            return ["MARKET_ENTRY_SCHEDULE_TIMESTAMP_MISMATCH"]
        limit = float(interval.get("limit_price"))
    except (TypeError, ValueError):
        return ["MARKET_ENTRY_SCHEDULE_INVALID"]
    row = None
    for candidate in rows:
        try:
            candidate_ts = int(float(candidate.get("bucket_ts")))
        except (TypeError, ValueError):
            continue
        if candidate_ts == expected_ts:
            row = candidate
            break
    if row is None:
        return []  # the evidence gate emits the more precise missing-BBO code
    quote_key = "ask" if str(episode.get("direction")).upper() == "LONG" else "bid"
    try:
        quote = float(row.get(quote_key))
    except (TypeError, ValueError):
        return ["MARKET_ENTRY_SIDE_QUOTE_MISSING"]
    if limit != quote:
        return ["MARKET_ENTRY_LIMIT_NOT_BOUND_TO_SIDE_QUOTE"]
    return []


def _chase_window_schedule_failures(
    baseline: Mapping[str, Any], episode: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> list[str]:
    """Require a window treatment's signed schedule to stay in that window."""
    if baseline.get("entry_type") != "LIMIT_CHASE_WINDOW":
        return []
    try:
        signal_ts = int(float(episode.get("signal_ts")))
        window_start = signal_ts + int(baseline["window_start_sec"])
        window_end = signal_ts + int(baseline["window_end_sec"])
    except (KeyError, TypeError, ValueError):
        return ["CHASE_WINDOW_TIMESTAMP_MISSING"]
    schedule = envelope.get("schedule") or []
    for interval in schedule:
        try:
            start_ts = int(interval.get("start_ts"))
            end_ts = int(interval.get("end_ts"))
        except (AttributeError, TypeError, ValueError):
            return ["CHASE_WINDOW_SCHEDULE_INVALID"]
        if start_ts < window_start or start_ts >= window_end or end_ts <= start_ts or end_ts > window_end:
            return ["CHASE_WINDOW_SCHEDULE_OUTSIDE_DECLARED_BUCKET"]
    return []


def _evidence_projection(
    baseline: Mapping[str, Any], episode: Mapping[str, Any],
    envelope: Mapping[str, Any], rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    constraints = episode.get("signed_quantity_constraints")
    qty = episode.get("requested_qty")
    remaining = episode.get("requested_remaining_qty")
    schedule = envelope.get("schedule") or []
    expiry_ts = episode.get("expiry_ts")
    return {
        "signal_time_bbo": _rows_cover_timestamp(rows, episode.get("signal_ts")),
        "expiry_time_bbo": _rows_cover_timestamp(rows, expiry_ts),
        "executable_depth": bool(rows) and all(
            row.get("ask_qty") is not None and row.get("bid_qty") is not None for row in rows
        ),
        "bbo_depth_trade_tape": rows,
        "requested_quantity": qty,
        "requested_remaining_quantity": remaining,
        "venue_quantity_constraints": constraints,
        "latency": episode.get("latency_sec"),
        "fees": episode.get("fees_usd"),
        "slippage": episode.get("slippage_model"),
        "signed_stage_receipts": envelope if schedule else None,
        "authoritative_final_schedule": envelope if schedule else None,
        "authoritative_parent_expiry": episode.get("authoritative_parent_expiry"),
    }


def replay_episode(episode: Mapping[str, Any], *, generation: Mapping | None = None) -> dict[str, Any]:
    """Replay all registered baselines against one causal opportunity."""
    episode_id = str(episode.get("episode_id") or "")
    opportunity_id = str(episode.get("opportunity_id") or "")
    if not episode_id or not opportunity_id:
        raise ValueError("EPISODE_AND_OPPORTUNITY_ID_REQUIRED")
    rows = [dict(row) for row in (episode.get("market_microstructure_rows") or []) if isinstance(row, Mapping)]
    canonical_identity_failures = [
        f"MISSING_{name.upper()}"
        for name in (
            "dataset_epoch", "source_revision", "tile_config_signature",
            "direction", "symbol",
        )
        if not str(episode.get(name) or "").strip()
    ]
    canonical_identity_failures.extend(
        str(code) for code in episode.get("materialization_reason_codes") or []
    )
    canonical_identity_failures.extend(
        str(code) for code in episode.get("market_evidence_reason_codes") or []
    )
    results = []
    for baseline in _baseline_rows():
        if canonical_identity_failures:
            results.append(_unknown(baseline, episode, *canonical_identity_failures))
            continue
        envelope, schedule_failures = _schedule_envelope(baseline, episode)
        if envelope is None or schedule_failures:
            results.append(_unknown(baseline, episode, *schedule_failures))
            continue
        market_failures = _market_schedule_failures(baseline, episode, envelope, rows)
        if market_failures:
            results.append(_unknown(baseline, episode, *market_failures))
            continue
        window_failures = _chase_window_schedule_failures(baseline, episode, envelope)
        if window_failures:
            results.append(_unknown(baseline, episode, *window_failures))
            continue
        evidence = _evidence_projection(baseline, episode, envelope, rows)
        gate = missing_baseline_evidence(baseline["baseline_id"], evidence)
        if not gate["complete"]:
            results.append(_unknown(baseline, episode, *gate["rejection_codes"]))
            continue
        requested_qty = (
            episode.get("requested_remaining_qty")
            if baseline["baseline_id"] == "FINAL_MARKET_AFTER_EXPIRY"
            else episode.get("requested_qty")
        )
        receipt = evaluate_limit_fill(
            rows,
            direction=str(episode.get("direction") or "UNKNOWN"),
            requested_qty=requested_qty,
            chase_schedule=envelope["schedule"],
            # Each one-second bucket is independently executable from its
            # fresh opposite BBO.  A wider aggressor context would cross a
            # reprice boundary for one-second market-entry schedules and turn
            # exact BBO evidence into artificial interval ambiguity.
            aggressor_window_sec=1,
            symbol=str(episode.get("symbol") or ""),
            quantity_constraints=episode.get("signed_quantity_constraints"),
        )
        receipt["declared_fees_usd"] = episode.get("fees_usd")
        receipt["measured_input_latency_sec"] = episode.get("latency_sec")
        receipt["declared_slippage_model"] = episode.get("slippage_model")
        terminal = str(receipt.get("final_classification") or "").upper()
        if receipt.get("supported") is not True or terminal not in {
            "FULL_FILL", "PARTIAL_FILL", "NO_FILL",
        }:
            results.append(_unknown(
                baseline, episode,
                *(receipt.get("negative_reasons") or ["CONSERVATIVE_EVALUATOR_UNSUPPORTED"]),
            ))
            continue
        results.append({
            "baseline_id": baseline["baseline_id"],
            "policy_signature": baseline["policy_signature"],
            "episode_id": episode_id,
            "opportunity_id": opportunity_id,
            "outcome_state": terminal,
            "supported": True,
            "rejection_codes": [],
            "conservative_receipt": receipt,
            "baseline_spec": dict(baseline),
            "schedule_provenance": {
                "capture_status": envelope.get("capture_status"),
                "capture_basis": envelope.get("capture_basis"),
                "evaluated_schedule_sha256": receipt.get("schedule_sha256"),
            },
        })
        receipt["simulation_model"] = receipt.get("evaluator_version")
        receipt["cost_model_id"] = episode.get("cost_model_id")
        receipt["tape_hashes"] = sorted(set(episode.get("market_tape_hashes") or []))
        receipt["tape_ids"] = sorted(set(episode.get("market_tape_ids") or []))
        receipt["market_evidence_provenance"] = list(
            episode.get("market_evidence_provenance") or []
        )
        if generation is not None and terminal in {"FULL_FILL", "PARTIAL_FILL"}:
            # The evaluator validates this exact symbol against every tape
            # row/lot receipt, but does not project it in its base envelope.
            receipt["symbol"] = str(episode.get("symbol") or "")
            projection = _execution_context(episode, results[-1], generation)
            results[-1]["model_context_status"] = projection["status"]
            results[-1]["model_context_blockers"] = projection["reason_codes"]
            if projection["context"] is not None:
                results[-1]["execution_model_context"] = projection["context"]
    material = {
        "schema": EPISODE_RECEIPT_SCHEMA,
        "episode_id": episode_id,
        "opportunity_id": opportunity_id,
        "dataset_epoch": episode.get("dataset_epoch"),
        "source_revision": episode.get("source_revision"),
        "deployed_revision": episode.get("deployed_revision"),
        "tile_config_signature": episode.get("tile_config_signature"),
        "config_signature": episode.get("config_signature"),
        "direction": episode.get("direction"),
        "market": episode.get("market"),
        "symbol": episode.get("symbol"),
        "regime": episode.get("regime") or episode.get("market_regime"),
        "regime_features_at_signal": episode.get("regime_features_at_signal"),
        "causal_identity": episode.get("causal_identity"),
        "market_tape_hashes": sorted(set(episode.get("market_tape_hashes") or [])),
        "market_tape_ids": sorted(set(episode.get("market_tape_ids") or [])),
        "market_evidence_provenance": list(episode.get("market_evidence_provenance") or []),
        "market_evidence_reason_codes": list(episode.get("market_evidence_reason_codes") or []),
        "materialization_reason_codes": list(episode.get("materialization_reason_codes") or []),
        "future_path_selection": episode.get("future_path_selection"),
        "baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "results": results,
    }
    if generation is not None:
        material["generation"] = dict(generation)
        material["model_context_source_resolution"] = episode.get("_baseline_context_source_resolution") or []
    material["receipt_id"] = canonical_hash("entry-baseline-episode", material)
    return material


def materialize_same_opportunity_replay(
    episodes: Iterable[Mapping[str, Any]],
    *, generation: Mapping | None = None,
) -> dict[str, Any]:
    """Return deterministic episode receipts plus comparable outcome counts."""
    receipts = [replay_episode(episode, generation=generation) for episode in episodes]
    receipts.sort(key=lambda row: (str(row["opportunity_id"]), str(row["episode_id"])))
    expected = [row["baseline_id"] for row in _baseline_rows()]
    summaries = {}
    for baseline_id in expected:
        states = Counter(
            result["outcome_state"]
            for receipt in receipts for result in receipt["results"]
            if result["baseline_id"] == baseline_id
        )
        summaries[baseline_id] = {
            "opportunities": len(receipts),
            "full_fills": states["FULL_FILL"],
            "partial_fills": states["PARTIAL_FILL"],
            "no_fills": states["NO_FILL"],
            "unknown": states["UNKNOWN"],
        }
    material = {
        "schema": REPLAY_SCHEMA,
        "baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "same_opportunity_count": len(receipts),
        "baseline_ids": expected,
        "summaries": summaries,
        "episode_receipts": receipts,
        "analysis_scope": "ENTRY_FILL_COUNTERFACTUAL_ONLY",
        "terminal_exit_pnl_evaluated": False,
        "profitability_supported": False,
        "profitability_blocker": "BASELINE_EXIT_AND_COST_COMPLETE_TERMINAL_RECEIPT_NOT_IMPLEMENTED",
        "relay_eligible": False,
    }
    if generation is not None:
        material["generation"] = dict(generation)
    material["report_id"] = canonical_hash("entry-baseline-replay", material)
    return material


def materialize_v3_opportunity_replay(data_dir: str | Path, *, incident_input=None,
        generation: Mapping | None = None, canonical_manifest: Mapping | None = None) -> dict[str, Any]:
    with VerifiedLedgerRowIndex() if generation is not None else nullcontext(None) as context_index:
        return _materialize_v3_opportunity_replay(data_dir, incident_input=incident_input,
            generation=generation, canonical_manifest=canonical_manifest, context_index=context_index)


def _materialize_v3_opportunity_replay(data_dir: str | Path, *, incident_input=None,
        generation: Mapping | None = None, canonical_manifest: Mapping | None = None,
        context_index: VerifiedLedgerRowIndex | None = None) -> dict[str, Any]:
    """Materialize the canonical opportunity cohort through this replay engine.

    Missing joins remain explicit UNKNOWN results; no schedule or market data is
    reconstructed from later evidence.
    """
    from research.runtime_identity_incidents import load_incident_input, IncidentEpisodeIndex
    incident_input = incident_input if incident_input is not None else load_incident_input()
    incident_index = IncidentEpisodeIndex(incident_input)
    v3_root = Path(data_dir) / "v3"
    context_pins, context_records, context_pin_reasons, context_snapshot = ({}, {}, [], None)
    context_source_resolution = []
    if generation is not None:
        context_pins, context_records, context_pin_reasons, context_snapshot = _context_source_pins(
            Path(data_dir), generation, canonical_manifest)
        context_source_resolution = _context_ledger_sources(Path(data_dir), context_pins, context_records, context_index)
        if "v3/ledgers/opportunity.jsonl" not in context_index.sources:
            context_pin_reasons.append("BASELINE_CONTEXT_OPPORTUNITY_SOURCE_NOT_VERIFIED")
    if incident_input.enabled:
        # Include all policy variants and terminal evidence, not just the
        # selected entry tape. Unresolvable linked rows fail closed.
        for ledger in ("opportunity", "decision", "order_intent", "execution", "lifecycle"):
            ledger_path = v3_root / "ledgers" / f"{ledger}.jsonl"
            if ledger_path.is_file():
                with ledger_path.open("r", encoding="utf-8-sig") as handle:
                    for line in handle:
                        if line.strip():
                            linked = json.loads(line)
                            if not isinstance(linked, Mapping):
                                raise ValueError("IDENTITY_INCIDENT_LEDGER_ROW_INVALID")
                            incident_index.add([linked])
    path = v3_root / "ledgers" / "opportunity.jsonl"
    segment_rows: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for segment_path in (
        v3_root / "ledgers" / "market_segment.jsonl",
        v3_root / "recovery_ledgers" / "market_segment.jsonl",
    ):
        if not segment_path.is_file():
            continue
        with segment_path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    segment = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(segment, dict):
                    continue
                incident_index.add([segment])
                key = tuple(str(segment.get(field) or "") for field in (
                    "epoch_id", "opportunity_id", "episode_id"
                ))
                segment_rows.setdefault(key, []).append(segment)
    episodes = []
    if path.is_file():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(row, Mapping):
                    continue
                snapshot = row.get("baseline_schedule_snapshot")
                schedules = (
                    snapshot.get("schedules")
                    if isinstance(snapshot, Mapping) else row.get("baseline_schedules")
                )
                causal = row.get("causal_identity")
                causal = causal if isinstance(causal, Mapping) else {}
                identity_reasons: list[str] = []

                def consistent(name: str, *values: Any) -> Any:
                    present = [value for value in values if value not in (None, "")]
                    normalized = {str(value).strip() for value in present}
                    if len(normalized) > 1:
                        identity_reasons.append(f"CONFLICTING_CAUSAL_IDENTITY:{name}")
                        return None
                    return present[0] if present else None

                dataset_epoch = consistent(
                    "dataset_epoch", row.get("dataset_epoch"), row.get("epoch_id"),
                    causal.get("dataset_epoch"),
                )
                source_revision = consistent(
                    "source_revision", row.get("source_revision"), row.get("source_git_rev"),
                    causal.get("source_revision"),
                )
                deployed_revision = consistent(
                    "deployed_revision", row.get("deployed_revision"), causal.get("deployed_revision"),
                )
                tile_signature = consistent(
                    "tile_config_signature", row.get("tile_config_signature"),
                    causal.get("tile_config_signature"),
                )
                direction = consistent(
                    "direction", row.get("direction"), row.get("raw_direction"), causal.get("direction"),
                )
                market = consistent("market", row.get("market"), causal.get("market"))
                symbol = consistent("symbol", row.get("symbol"), causal.get("symbol"))
                key = tuple(str(value or "") for value in (
                    row.get("epoch_id") or row.get("dataset_epoch"),
                    row.get("opportunity_id") or row.get("record_id"),
                    row.get("episode_id"),
                ))
                market_rows: list[dict[str, Any]] = []
                tape_hashes: list[str] = []
                tape_ids: list[str] = []
                evidence_provenance: list[dict[str, Any]] = []
                evidence_reasons: list[str] = []
                context_coverage = []
                selected, history = authoritative_future_path_segments(
                    v3_root, segment_rows.get(key, [])
                )
                for segment in selected:
                    role = segment_role(segment)
                    coverage = segment.get("coverage") if isinstance(segment.get("coverage"), Mapping) else {}
                    eligible = (
                        role == ALL_OPPORTUNITY_FUTURE_ROLE
                        and complete_conservative_future_path(segment)
                    ) or (
                        role in {"ENTRY_PATH", "ENTRY_AND_EXIT_PATH", "FULL_LIFECYCLE"}
                        and coverage.get("conservative_bbo_depth_eligible") is True
                    )
                    if not eligible:
                        continue
                    digest, errors = _verify_segment(v3_root, segment)
                    if errors or not digest:
                        evidence_reasons.extend(errors)
                        evidence_provenance.append({
                            "segment_record_id": segment.get("record_id"),
                            "declared_segment_ref": segment.get("segment_ref"),
                            "status": "UNKNOWN", "reason_codes": errors,
                        })
                        continue
                    ref = segment["segment_ref"]
                    object_path = (v3_root.parent / str(ref["relative_path"])).resolve()
                    try:
                        object_bytes = object_path.read_bytes()
                        if hashlib.sha256(object_bytes).hexdigest() != digest:
                            raise ValueError("CHECKSUM_CHANGED_AFTER_VERIFICATION")
                        envelope = json.loads(object_bytes.decode("utf-8-sig"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        evidence_reasons.append("UNKNOWN_TAPE_ENVELOPE_INVALID")
                        evidence_provenance.append({
                            "segment_record_id": segment.get("record_id"),
                            "sha256": digest, "status": "UNKNOWN",
                            "reason_codes": ["UNKNOWN_TAPE_ENVELOPE_INVALID"],
                        })
                        continue
                    except ValueError as exc:
                        evidence_reasons.append(str(exc))
                        evidence_provenance.append({
                            "segment_record_id": segment.get("record_id"),
                            "sha256": digest, "relative_path": ref.get("relative_path"),
                            "status": "UNKNOWN", "reason_codes": [str(exc)],
                        })
                        continue
                    evidence_rows = envelope.get("rows") if isinstance(envelope, Mapping) else None
                    if not isinstance(evidence_rows, list) or not evidence_rows:
                        evidence_reasons.append("UNKNOWN_TAPE_ROWS_MISSING")
                        evidence_provenance.append({
                            "segment_record_id": segment.get("record_id"),
                            "sha256": digest, "status": "UNKNOWN",
                            "reason_codes": ["UNKNOWN_TAPE_ROWS_MISSING"],
                        })
                        continue
                    market_rows.extend(dict(item) for item in evidence_rows if isinstance(item, Mapping))
                    if generation is not None:
                        # Object identity is established by the separately
                        # pinned ledger binding, never injected into its bytes.
                        relative = str(ref["relative_path"])
                        for binding_relative in ("v3/ledgers/market_segment.jsonl", "v3/recovery_ledgers/market_segment.jsonl"):
                            if binding_relative not in context_pins:
                                continue
                            binding_proof = context_index.envelope(binding_relative, segment)
                            if binding_proof is None:
                                continue
                            if context_pins.get(relative) != digest:
                                continue
                            context_coverage.append({"object": _context_envelope(relative, object_bytes, envelope),
                                "binding": binding_proof})
                            break
                    tape_hashes.append(digest)
                    tape_ids.append(str(segment.get("record_id") or ""))
                    evidence_provenance.append({
                        "segment_record_id": segment.get("record_id"),
                        "sha256": digest, "relative_path": ref.get("relative_path"),
                        "context_role": role, "status": "VERIFIED",
                    })
                if not tape_hashes:
                    evidence_reasons.append("NO_MATCHING_VERIFIED_MARKET_SEGMENT")
                episode_context_sources = []
                episode_context_reasons = list(context_pin_reasons)
                if context_index:
                    try:
                        episode_context_sources = context_index.identity_envelopes(tuple(str(row.get(field) or (
                            dataset_epoch if field == "epoch_id" else "")) for field in IDENTITY_FIELDS))
                    except ValueError as exc:
                        episode_context_reasons.append(str(exc))
                episodes.append({
                    **dict(row),
                    "opportunity_id": row.get("opportunity_id") or row.get("record_id"),
                    "dataset_epoch": dataset_epoch,
                    "source_revision": source_revision,
                    "deployed_revision": deployed_revision,
                    "tile_config_signature": tile_signature,
                    "direction": direction,
                    "market": market,
                    "symbol": symbol,
                    "baseline_schedules": schedules,
                    "market_microstructure_rows": market_rows,
                    "market_tape_hashes": sorted(set(tape_hashes)),
                    "market_tape_ids": sorted(filter(None, set(tape_ids))),
                    "market_evidence_provenance": evidence_provenance,
                    "market_evidence_reason_codes": sorted(set(evidence_reasons)),
                    "materialization_reason_codes": sorted(set(identity_reasons + incident_index.reasons(row) + (
                        ["UNKNOWN_DEPLOYED_SOURCE_IDENTITY_INCIDENT"]
                        if any(incident_input.affected(item) for item in market_rows) else []
                    ))),
                    "future_path_selection": history,
                    "_baseline_context_sources": episode_context_sources,
                    "_baseline_context_pins": context_pins,
                    "_baseline_context_pin_reasons": episode_context_reasons,
                    "_baseline_context_coverage": context_coverage,
                    "_baseline_context_source_resolution": context_source_resolution,
                    "_baseline_context_opportunity": context_index.envelope("v3/ledgers/opportunity.jsonl", row) if context_index else None,
                })
    for episode in episodes:
        if "UNKNOWN_DEPLOYED_SOURCE_IDENTITY_INCIDENT" in episode.get("materialization_reason_codes", []):
            incident_index.add([{**episode, "timestamp": None}])
    for episode in episodes:
        episode["materialization_reason_codes"] = sorted(set(
            episode.get("materialization_reason_codes", []) + incident_index.reasons(episode)
        ))
    report = materialize_same_opportunity_replay(episodes, generation=generation)
    if incident_input.enabled:
        report["runtime_identity_incident_input"] = incident_input.provenance()
        report["runtime_identity_incident_coverage"] = incident_index.coverage()
        report["report_id"] = canonical_hash("entry-baseline-replay", {
            key: value for key, value in report.items() if key != "report_id"
        })
    incident_input.assert_unchanged()
    if context_index is not None:
        context_index.assert_sources_unchanged()
    if context_snapshot is not None and (
        (Path(data_dir) / "canonical_dataset_current.json").read_bytes() != context_snapshot[0]
        or (Path(data_dir) / ".fly-sync-state.json").read_bytes() != context_snapshot[1]
    ):
        raise ValueError("BASELINE_CONTEXT_PINNED_GENERATION_CHANGED_DURING_REPLAY")
    return report
