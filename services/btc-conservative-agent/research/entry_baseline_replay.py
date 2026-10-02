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
import re

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
    lifecycle_sources = sorted(name for name in pins
        if re.fullmatch(r"v3/ledgers/lifecycle\.jsonl(?:\.[1-9][0-9]*)?", name))
    if len(lifecycle_sources) > 1024:
        raise ValueError("SIGNAL_SNAPSHOT_LIFECYCLE_SOURCE_LIMIT")
    for relative in ("chase_offset_touch_grid.jsonl", "chase_offset_touch_grid.jsonl.1",
                     "v3/ledgers/baseline_execution_context.jsonl", "v3/ledgers/opportunity.jsonl",
                     "v3/ledgers/pre_entry_features.jsonl",
                     "v3/ledgers/market_segment.jsonl", "v3/recovery_ledgers/market_segment.jsonl",
                     *lifecycle_sources):
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


def _causal_feature_projection(opportunity: Mapping, index: VerifiedLedgerRowIndex | None) -> dict:
    """Join immutable pre-decision features only through the pinned row index."""
    from research.research_v3_report import join_pre_entry_feature_receipts
    from research.policy_evidence_evaluator import _regime_features_at_signal

    source = "v3/ledgers/pre_entry_features.jsonl"
    identity = tuple(str(opportunity.get(field) or "") for field in (
        "epoch_id", "opportunity_id", "episode_id"))
    proofs = []
    blocker = None
    opportunity_proof = None
    if index is None or source not in index.sources:
        blocker = "PRE_ENTRY_FEATURE_SOURCE_NOT_VERIFIED"
    else:
        opportunity_proof = index.envelope("v3/ledgers/opportunity.jsonl", opportunity)
        if opportunity_proof is None or not all(identity):
            blocker = "PRE_ENTRY_OPPORTUNITY_MEMBERSHIP_NOT_VERIFIED"
        else:
            proofs = index.pre_entry_feature_envelopes(*identity)
            if any(
                str(opportunity.get(field) or "").strip()
                and str(proof["row"].get(field) or "").strip()
                and str(opportunity[field]).strip() != str(proof["row"][field]).strip()
                for proof in proofs
                for field in ("source_revision", "deployed_revision", "tile_config_signature", "config_signature")
            ):
                blocker = "PRE_ENTRY_CAUSAL_PROVENANCE_MISMATCH"
    joined, _ = join_pre_entry_feature_receipts(
        [dict(opportunity)], [] if blocker else [proof["row"] for proof in proofs],
    )
    row = joined[0]
    if blocker:
        row["pre_entry_feature_blockers"] = [blocker]
    taxonomy = {name: None for name in (
        "bucket_definition_signature", "bucket_definition_schema", "bucket_definition_version")}
    taxonomy_blockers = []
    if blocker or len(proofs) != 1 or row["pre_entry_feature_status"] != "COMPLETE":
        taxonomy_blockers.append("PRE_ENTRY_BUCKET_RECEIPT_NOT_UNIQUE_VERIFIED_CAUSAL")
    else:
        receipt = proofs[0]["row"]
        signature = receipt.get("bucket_definition_signature")
        if (not isinstance(signature, str) or not signature.strip()
                or signature.strip().upper() in {"UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "MISSING"}):
            taxonomy_blockers.append("PRE_ENTRY_BUCKET_SIGNATURE_MISSING_OR_INVALID")
        elif any(opportunity.get(name) not in (None, "") and opportunity[name] != receipt.get(name)
                 for name in taxonomy):
            taxonomy_blockers.append("PRE_ENTRY_BUCKET_DEFINITION_CONFLICT")
        else:
            # Preserve the exact historical definition identity, never today's registry.
            taxonomy = {name: receipt.get(name) for name in taxonomy}
    snapshot = opportunity.get("feature_snapshot_at_signal")
    return {
        **taxonomy,
        "bucket_definition_status": "VERIFIED" if not taxonomy_blockers else "UNKNOWN",
        "bucket_definition_blockers": taxonomy_blockers,
        "pre_entry_features": row["pre_entry_features"],
        "pre_entry_feature_status": row["pre_entry_feature_status"],
        "pre_entry_feature_blockers": row["pre_entry_feature_blockers"],
        "regime_features_at_signal": _regime_features_at_signal(
            row, snapshot if isinstance(snapshot, Mapping) else {}),
        "pre_entry_feature_evidence": {
            "source_id": source, "source_sha256": index.sources.get(source) if index else None,
            "opportunity_row_sha256": opportunity_proof["row_sha256"] if opportunity_proof else None,
            "receipt_row_sha256": proofs[0]["row_sha256"] if len(proofs) == 1 else None,
            "matching_receipts_ambiguous": len(proofs) > 1,
            "status": row["pre_entry_feature_status"],
            "reason_codes": row["pre_entry_feature_blockers"],
            "bucket_definition_signature": taxonomy["bucket_definition_signature"],
            "bucket_definition_status": "VERIFIED" if not taxonomy_blockers else "UNKNOWN",
            "bucket_definition_blockers": taxonomy_blockers,
        },
    }


def _signal_snapshot_projection(root: Path, pins: Mapping, records: Mapping,
                                index: VerifiedLedgerRowIndex | None, *, epoch: str, episode: str,
                                opportunity: Mapping) -> dict:
    """Expose event-scoped first-capture evidence without rewriting opportunity truth."""
    result = {"schema": "verified_signal_snapshot_projection_v1", "status": "UNAVAILABLE",
              "contexts": [], "reason_codes": [], "observed_at_signal_claim": False,
              "fill_atr_authority": False, "qualification_allowed": False}
    if index is None:
        result["reason_codes"] = ["SIGNAL_SNAPSHOT_PINNED_GENERATION_UNAVAILABLE"]
        return result
    # The normal materializer rereads opportunities independently of this
    # verified index. Never attach pinned event evidence to an unpinned row
    # merely because that row happens to reuse a valid epoch/episode pair.
    opportunity_source = "v3/ledgers/opportunity.jsonl"
    opportunity_proof = (index.envelope(opportunity_source, opportunity)
                         if opportunity_source in index.sources else None)
    if (opportunity_proof is None or opportunity.get("epoch_id") != epoch
            or opportunity.get("episode_id") != episode):
        result.update(status="UNKNOWN", reason_codes=["SIGNAL_SNAPSHOT_OPPORTUNITY_MEMBERSHIP_NOT_VERIFIED"])
        return result
    result["source_opportunity_row"] = {
        "source_id": opportunity_source, "source_sha256": index.sources[opportunity_source],
        "record_id": opportunity.get("record_id"), "row_sha256": opportunity_proof["row_sha256"],
    }
    lifecycle_sources = [name for name in pins
        if re.fullmatch(r"v3/ledgers/lifecycle\.jsonl(?:\.[1-9][0-9]*)?", name)]
    if not lifecycle_sources:
        result["reason_codes"] = ["SIGNAL_SNAPSHOT_LIFECYCLE_SOURCE_UNAVAILABLE"]
        return result
    if any(name not in index.sources for name in lifecycle_sources):
        result.update(status="UNKNOWN", reason_codes=["SIGNAL_SNAPSHOT_LIFECYCLE_SOURCE_NOT_VERIFIED"])
        return result
    try:
        envelopes = index.lifecycle_envelopes(epoch, episode)
    except ValueError as exc:
        result.update(status="UNKNOWN", reason_codes=[str(exc)])
        return result
    groups = {}
    projection_bytes = 0
    from collector_signal_snapshot import load_signal_snapshot, MAX_SNAPSHOT_BYTES
    for envelope in envelopes:
        row = envelope["row"]
        if "research_signal_snapshot_ref" not in row:
            continue
        ref = row["research_signal_snapshot_ref"]
        event_id = row.get("event_id")
        key = (str(event_id), canonical_json({"reference": ref, "signal_ts": row.get("signal_ts")}))
        membership = {"source_id": envelope["source_id"], "source_sha256": index.sources[envelope["source_id"]],
                      "record_id": row.get("record_id"), "row_sha256": envelope["row_sha256"]}
        if key in groups:
            groups[key]["source_lifecycle_rows"].append(membership)
            continue
        projected = {"event_id": event_id, "epoch_id": epoch, "signal_ts": row.get("signal_ts"),
                     "reference": ref, "status": "UNKNOWN", "reason_codes": [],
                     "source_lifecycle_rows": [membership], "observed_at_signal_claim": False}
        groups[key] = projected
        try:
            if not isinstance(ref, Mapping):
                raise ValueError("SIGNAL_SNAPSHOT_REFERENCE_INVALID")
            relative = ref.get("relative_path")
            if not isinstance(relative, str) or relative not in pins:
                raise ValueError("SIGNAL_SNAPSHOT_NOT_IN_PINNED_DATASET")
            if pins[relative] != ref.get("sha256") or records[relative].get("size") != ref.get("bytes"):
                raise ValueError("SIGNAL_SNAPSHOT_MANIFEST_REFERENCE_MISMATCH")
            index.read_pinned_object(root, relative, expected_sha=pins[relative],
                                     expected_size=records[relative].get("size"), max_bytes=MAX_SNAPSHOT_BYTES)
            snapshot = load_signal_snapshot(ref, data_dir=root, event_id=event_id,
                                             epoch_id=epoch, signal_ts=row.get("signal_ts"))
            if snapshot.get("capture_basis") != "FIRST_COLLECTOR_CAPTURE":
                raise ValueError("SIGNAL_SNAPSHOT_CAPTURE_BASIS_INVALID")
            projection_bytes += len(canonical_json(snapshot["evidence"]).encode())
            if projection_bytes > 8 * 1024 * 1024:
                raise ValueError("SIGNAL_SNAPSHOT_PROJECTION_GROUP_LIMIT")
            projected.update(status="VERIFIED_FIRST_COLLECTOR_CAPTURE", fields=snapshot["evidence"],
                             captured_at=snapshot.get("captured_at"), capture_basis=snapshot.get("capture_basis"),
                             availability_at_signal_verified=False)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "SIGNAL_SNAPSHOT_OBJECT_UNAVAILABLE"
            if reason == "SIGNAL_SNAPSHOT_PROJECTION_GROUP_LIMIT":
                result.update(status="UNKNOWN", contexts=[], reason_codes=[reason])
                return result
            projected["reason_codes"] = [reason]
    events = Counter(key[0] for key in groups)
    for (event, _), projected in groups.items():
        if events[event] > 1:
            projected.update(status="UNKNOWN", reason_codes=sorted(set(projected["reason_codes"] + ["SIGNAL_SNAPSHOT_EVENT_REFERENCE_CONFLICT"])))
    result["contexts"] = list(groups.values())
    result["status"] = ("UNKNOWN" if any(row["status"] == "UNKNOWN" for row in groups.values())
                        else "VERIFIED_FIRST_COLLECTOR_CAPTURE" if groups else "UNAVAILABLE")
    result["reason_codes"] = sorted({code for row in groups.values() for code in row["reason_codes"]})
    if not groups:
        result["reason_codes"] = ["SIGNAL_SNAPSHOT_NO_DECLARED_REFERENCE"]
    return result


def _execution_context(episode: Mapping, result: Mapping, generation: Mapping) -> dict:
    capture = episode.get("directional_capture") or {}
    if capture.get("research_context_declaration") is not None:
        from research.baseline_execution_context import build_declared_directional_baseline_context
        coverage = episode.get("_baseline_context_coverage") or []
        if len(coverage) != 1 or episode.get("_baseline_context_pin_reasons"):
            return {"status": "UNKNOWN", "context": None,
                    "reason_codes": ["DECLARED_BASELINE_SINGLE_VERIFIED_COVERAGE_REQUIRED"]}
        identity = {key: episode.get(key) for key in IDENTITY_FIELDS}
        identity.update(epoch_id=episode.get("epoch_id") or episode.get("dataset_epoch"),
                        direction=episode.get("direction"), symbol=episode.get("symbol"),
                        baseline_id=result["baseline_id"], baseline_policy_signature=result["policy_signature"])
        return build_declared_directional_baseline_context(generation=generation, identity=identity,
            entry_receipt=result["conservative_receipt"], capture=capture, baseline=result["baseline_spec"],
            pinned_sources=episode.get("_baseline_context_pins") or {},
            opportunity_binding=episode.get("_baseline_context_opportunity"),
            coverage_evidence=coverage[0]["object"], coverage_binding=coverage[0].get("binding"))
    if episode.get("counterfactual_direction") is True:
        return {"status": "UNKNOWN", "context": None,
                "reason_codes": ["DIRECTION_SPECIFIC_BASELINE_EXECUTION_CONTEXT_REQUIRED"]}
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
    if episode.get("directional_capture") is not None:
        capture = episode["directional_capture"]
        if capture.get("direction") != episode.get("direction") or capture.get("schedules") != schedules:
            failures.append("DIRECTIONAL_BASELINE_SCHEDULE_BINDING_MISMATCH")
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


def delayed_variant_cohorts(report):
    if not isinstance(report, Mapping) or not isinstance(report.get('episode_receipts', []), list):
        raise ValueError('DELAYED_VARIANT_SHAPE_INVALID')
    groups={}
    for episode in report.get('episode_receipts') or []:
        if not isinstance(episode, Mapping) or not isinstance(episode.get('delayed_variants', []), list):
            raise ValueError('DELAYED_VARIANT_SHAPE_INVALID')
        seen=set()
        for variant in episode.get('delayed_variants') or []:
            if not isinstance(variant, Mapping) or not isinstance(variant.get('results'), list):
                raise ValueError('DELAYED_VARIANT_SHAPE_INVALID')
            key=variant.get('timing_model_sha256')
            if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key) or key in seen:
                raise ValueError('DELAYED_VARIANT_IDENTITY_CONFLICT')
            seen.add(key)
            groups.setdefault(key,[]).append({**episode,'results':variant['results'],'delayed_variants':[]})
    return {key:{**report,'episode_receipts':episodes,'timing_model_sha256':key}
            for key,episodes in sorted(groups.items())}


def _declared_delayed_variants(episode, generation):
    """Separate timing cohorts; never overwrite original baseline identities."""
    from research.baseline_execution_context import (_verified, _sha,
        declared_directional_baseline_inputs, build_declared_delayed_baseline_context, verified_segment_rows)
    from research.latency_schedule_replay import replay_delayed_entry
    if generation is None or not episode.get('directional_capture'):
        return []
    pins=episode.get('_baseline_context_pins') or {}
    try:
        opportunity,_=_verified(episode.get('_baseline_context_opportunity'),pins)
    except (ValueError,TypeError,KeyError,AttributeError):
        return []
    capture=episode['directional_capture']
    declarations=opportunity.get('research_timing_declarations') or []
    if not isinstance(declarations,list) or len(declarations)>64:
        return [{'status':'UNKNOWN','reason_codes':['TIMING_DECLARATION_LIST_INVALID'],'results':[]}]
    variants=[]
    for timing in declarations:
        if not isinstance(timing,Mapping) or timing.get('source_capture_signature')!=capture.get('capture_signature'):
            continue
        try:
            model_hash=_sha({key:timing.get(key) for key in ('schema','delay_sec','ordering_treatment','evidence_basis')})
        except (TypeError,ValueError):
            continue
        variant={'timing_model_sha256':model_hash,'qualification_eligible':False,'results':[]}
        coverage=episode.get('_baseline_context_coverage') or []
        for baseline in _baseline_rows():
            result=_unknown(baseline,episode,'DELAYED_ENTRY_SOURCE_UNAVAILABLE')
            try:
                if not coverage or episode.get('_baseline_context_pin_reasons'):
                    raise ValueError('DELAYED_SINGLE_PINNED_SEGMENT_REQUIRED')
                evidence=[item['object'] for item in coverage]
                bindings=[item['binding'] for item in coverage]
                tape,_=verified_segment_rows(evidence,bindings,pins,
                    {key:opportunity.get(key) for key in IDENTITY_FIELDS},capture['symbol'])
                inputs=declared_directional_baseline_inputs(capture,baseline)
                replay=replay_delayed_entry(schedule=capture['schedules'][baseline['baseline_id']]['schedule'],
                    delay_sec=timing.get('delay_sec'),ordering_treatment=timing.get('ordering_treatment'),
                    tape=tape,direction=capture['direction'],requested_qty=inputs['requested_qty'],
                    quantity_constraints=inputs['signed_quantity_constraints'],symbol=capture['symbol'])
                if replay.get('status')!='ENTRY_REPLAY_SUPPORTED':
                    raise ValueError('DELAYED_ENTRY_REPLAY_UNSUPPORTED')
                receipt={**replay['entry_receipt'],'symbol':capture['symbol']}
                identity={key:episode.get(key) for key in IDENTITY_FIELDS}
                identity.update(epoch_id=generation['epoch_id'],direction=capture['direction'],symbol=capture['symbol'],
                    baseline_id=baseline['baseline_id'],baseline_policy_signature=baseline['policy_signature'])
                projection=build_declared_delayed_baseline_context(generation=generation,identity=identity,
                    capture=capture,baseline=baseline,pinned_sources=pins,
                    opportunity_binding=episode['_baseline_context_opportunity'],
                    coverage_evidence=evidence,coverage_binding=bindings,
                    entry_evidence=evidence,entry_binding=bindings,
                    timing_declaration=timing,tape=tape,delayed_replay_receipt=replay)
                result={'baseline_id':baseline['baseline_id'],'policy_signature':baseline['policy_signature'],
                    'episode_id':episode['episode_id'],'opportunity_id':episode['opportunity_id'],
                    'baseline_spec':dict(baseline),'conservative_receipt':receipt,
                    'supported':True,'outcome_state':receipt['final_classification'],
                    'model_context_status':projection['status'],'model_context_blockers':projection['reason_codes']}
                if projection['context'] is not None:
                    result['execution_model_context']=projection['context']
            except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):
                pass
            variant['results'].append(result)
        variants.append(variant)
    return variants


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
    source_episode = episode
    for baseline in _baseline_rows():
        episode = source_episode
        capture = episode.get("directional_capture") or {}
        if capture.get("research_context_declaration") is not None:
            from research.baseline_execution_context import declared_directional_baseline_inputs
            try:
                declared_inputs = declared_directional_baseline_inputs(capture, baseline)
                episode = {**episode, **{key: declared_inputs[key] for key in (
                    "requested_qty", "signed_quantity_constraints", "latency_sec", "fees_usd", "slippage_model")}}
            except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError) as exc:
                results.append(_unknown(baseline, episode, str(exc)))
                continue
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
        if capture.get("research_context_declaration") is not None:
            receipt["declared_input_latency_sec"] = episode.get("latency_sec")
            receipt["measured_input_latency_sec"] = None
            receipt["input_assumption_basis"] = "DECLARED_SIMULATION"
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
    episode = source_episode
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
        "source_episode_id": episode.get("source_episode_id") or episode_id,
        "original_ai_direction": episode.get("original_ai_direction") or episode.get("raw_direction") or episode.get("direction"),
        "source_execution_direction": episode.get("source_execution_direction", "UNKNOWN"),
        "counterfactual_direction": episode.get("counterfactual_direction") is True,
        "directional_capture_signature": (episode.get("directional_capture") or {}).get("capture_signature"),
        "directional_coverage": episode.get("directional_coverage", "LEGACY_SINGLE_SIDE_ONLY"),
        "market": episode.get("market"),
        "symbol": episode.get("symbol"),
        "signal_ts": episode.get("signal_ts"),
        "regime": episode.get("regime") or episode.get("market_regime"),
        "regime_features_at_signal": episode.get("regime_features_at_signal"),
        "pre_entry_features": episode.get("pre_entry_features"),
        "pre_entry_feature_status": episode.get("pre_entry_feature_status"),
        "pre_entry_feature_blockers": episode.get("pre_entry_feature_blockers"),
        "pre_entry_feature_evidence": episode.get("pre_entry_feature_evidence"),
        "bucket_definition_signature": episode.get("bucket_definition_signature"),
        "bucket_definition_schema": episode.get("bucket_definition_schema"),
        "bucket_definition_version": episode.get("bucket_definition_version"),
        "bucket_definition_status": episode.get("bucket_definition_status"),
        "bucket_definition_blockers": episode.get("bucket_definition_blockers"),
        "causal_identity": episode.get("causal_identity"),
        "market_tape_hashes": sorted(set(episode.get("market_tape_hashes") or [])),
        "market_tape_ids": sorted(set(episode.get("market_tape_ids") or [])),
        "market_evidence_provenance": list(episode.get("market_evidence_provenance") or []),
        "market_evidence_reason_codes": list(episode.get("market_evidence_reason_codes") or []),
        "materialization_reason_codes": list(episode.get("materialization_reason_codes") or []),
        "future_path_selection": episode.get("future_path_selection"),
        "signal_snapshot_evidence": episode.get("signal_snapshot_evidence"),
        "baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "results": results,
        "delayed_variants": _declared_delayed_variants(source_episode, generation),
    }
    if generation is not None:
        material["generation"] = dict(generation)
        material["model_context_source_resolution"] = episode.get("_baseline_context_source_resolution") or []
    material["receipt_id"] = canonical_hash("entry-baseline-episode", material)
    return material


def _directional_episodes(episode: Mapping[str, Any]) -> list[dict[str, Any]]:
    snapshot = episode.get("baseline_schedule_snapshot") or {}
    captures = snapshot.get("directional_schedules")
    if not isinstance(captures, Mapping):
        return [{**episode, "directional_coverage": "LEGACY_SINGLE_SIDE_ONLY"}]
    output = []
    for side in ("LONG", "SHORT"):
        capture = captures.get(side)
        reasons = []
        if not isinstance(capture, Mapping):
            capture = {}
            reasons.append("DIRECTIONAL_BASELINE_CAPTURE_MISSING")
        body = {key: value for key, value in capture.items() if key != "capture_signature"}
        if capture.get("capture_signature") != canonical_hash("directional-entry-capture", body, length=64):
            reasons.append("DIRECTIONAL_BASELINE_CAPTURE_SIGNATURE_INVALID")
        expected = {"source_episode_id": episode.get("episode_id"),
                    "opportunity_id": episode.get("opportunity_id") or episode.get("record_id"),
                    "signal_ts": episode.get("signal_ts"), "direction": side,
                    "original_ai_direction": str(episode.get("raw_direction") or "UNKNOWN").upper(),
                    "source_execution_direction": str(episode.get("source_execution_direction") or episode.get("executed_direction") or episode.get("direction") or "UNKNOWN").upper(),
                    "epoch_id": episode.get("epoch_id") or episode.get("dataset_epoch"),
                    "source_revision": episode.get("source_revision"),
                    "deployed_revision": episode.get("deployed_revision"),
                    "tile_config_signature": episode.get("tile_config_signature"),
                    "baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"]}
        if any(capture.get(key) != value for key, value in expected.items()):
            reasons.append("DIRECTIONAL_BASELINE_CAPTURE_IDENTITY_MISMATCH")
        if not capture.get("episode_id"):
            reasons.append("DIRECTIONAL_BASELINE_EPISODE_ID_MISSING")
        source_episode = str(episode.get("episode_id") or "")
        output.append({**episode, "source_episode_id": source_episode,
            "episode_id": capture.get("episode_id") or f"unavailable:{source_episode}:{side}",
            "direction": side, "original_ai_direction": capture.get("original_ai_direction", "UNKNOWN"),
            "source_execution_direction": capture.get("source_execution_direction", "UNKNOWN"),
            "counterfactual_direction": capture.get("episode_id") != source_episode,
            "baseline_schedules": capture.get("schedules") or {}, "directional_capture": capture,
            "directional_coverage": "BOTH_SIDES_CAPTURED" if not reasons else "DIRECTIONAL_CAPTURE_INVALID",
            "materialization_reason_codes": list(episode.get("materialization_reason_codes") or []) + reasons})
    return output


def materialize_same_opportunity_replay(
    episodes: Iterable[Mapping[str, Any]],
    *, generation: Mapping | None = None,
) -> dict[str, Any]:
    """Return deterministic episode receipts plus comparable outcome counts."""
    sources = list(episodes)
    receipts = [replay_episode(variant, generation=generation)
                for episode in sources for variant in _directional_episodes(episode)]
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
            "opportunities": len(sources),
            "directional_evaluations": len(receipts),
            "full_fills": states["FULL_FILL"],
            "partial_fills": states["PARTIAL_FILL"],
            "no_fills": states["NO_FILL"],
            "unknown": states["UNKNOWN"],
        }
    material = {
        "schema": REPLAY_SCHEMA,
        "baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "same_opportunity_count": len(sources),
        "directional_episode_count": len(receipts),
        "independent_sample_basis": "SOURCE_OPPORTUNITY_NOT_DIRECTIONAL_VARIANTS",
        "directional_coverage": dict(Counter(row["directional_coverage"] for row in receipts)),
        "baseline_ids": expected,
        "summaries": summaries,
        "episode_receipts": receipts,
        "analysis_scope": "ENTRY_FILL_COUNTERFACTUAL_ONLY",
        "terminal_exit_pnl_evaluated": False,
        "profitability_supported": False,
        "profitability_blocker": "BASELINE_EXIT_AND_COST_COMPLETE_TERMINAL_RECEIPT_NOT_IMPLEMENTED",
        "relay_eligible": False,
        "signal_snapshot_coverage": dict(Counter(
            (receipt.get("signal_snapshot_evidence") or {}).get("status", "UNAVAILABLE") for receipt in receipts
        )),
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
                if isinstance(snapshot, Mapping) and snapshot.get("directional_capture_schema") == "both_direction_entry_baselines_v1":
                    # Store causal.direction denotes raw AI direction. An
                    # explicitly different executed side is not a conflict.
                    if row.get("raw_direction") not in (None, ""):
                        # Collector's causal projection retains only LONG/SHORT;
                        # explicit NO_TRADE is represented there as UNKNOWN.
                        # Keep the raw decision unchanged and admit only this
                        # exact producer-defined sentinel pair, never opposite sides.
                        if not (row.get("raw_direction") == "NO_TRADE" and causal.get("direction") == "UNKNOWN"):
                            consistent("raw_ai_direction", row.get("raw_direction"), causal.get("direction"))
                    direction = row.get("direction") or row.get("raw_direction")
                else:
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
                    **_causal_feature_projection(row, context_index),
                    "opportunity_id": row.get("opportunity_id") or row.get("record_id"),
                    "dataset_epoch": dataset_epoch,
                    "source_revision": source_revision,
                    "deployed_revision": deployed_revision,
                    "tile_config_signature": tile_signature,
                    "direction": direction,
                    "source_execution_direction": str(row.get("executed_direction") or row.get("direction") or "UNKNOWN").upper(),
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
                    "signal_snapshot_evidence": _signal_snapshot_projection(
                        Path(data_dir), context_pins, context_records, context_index,
                        epoch=str(dataset_epoch or ""), episode=str(row.get("episode_id") or ""),
                        opportunity=row,
                    ),
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
