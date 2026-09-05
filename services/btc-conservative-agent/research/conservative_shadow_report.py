"""Pure current-generation fanout for conservative full-lifecycle shadow research."""
from __future__ import annotations

import hashlib
import gzip
import io
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.conservative_shadow_terminal import evaluate_shadow_terminal
from research.policy_evidence_schema import canonical_json, stable_hash
from research_v3_contract import canonical_hash

SCHEMA = "generation_bound_conservative_shadow_report_v1"
GENERATION_FIELDS = (
    "manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
    "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key",
)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _generation(value: Any, label: str) -> tuple[dict[str, str], list[str]]:
    if not isinstance(value, Mapping):
        return {}, [f"{label}_GENERATION_MISSING"]
    result, blockers = {}, []
    for field in GENERATION_FIELDS:
        raw = value.get(field)
        if isinstance(raw, (bool, Mapping, list, tuple, set)):
            blockers.append(f"{label}_GENERATION_INVALID:{field}")
            continue
        text = str(raw or "").strip()
        if not text or text.upper() in {"UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "MISSING"}:
            blockers.append(f"{label}_GENERATION_MISSING:{field}")
            continue
        result[field] = text
    return result, blockers


def _unknown(generation: Mapping[str, Any], blockers: Sequence[str], **extra: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "status": "UNKNOWN", "generation": dict(generation),
        "blockers": sorted(set(str(item) for item in blockers if item)),
        "independent_episode_count": 0, "candidate_policy_count": 0,
        "candidate_replay_count": 0, "complete_replay_count": 0,
        "unknown_replay_count": 0, "reason_counts": {}, "results": [],
        "profitability_supported": False, "ranking_eligible": False,
        "live_qualification": False, **extra,
    }


def _signed(label: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return {**dict(body), "signature": stable_hash(label, body)}


def _context_defects(context: Mapping[str, Any]) -> list[str]:
    """Require explicit model/provenance inputs before signing derived receipts."""
    text_fields = (
        "position_context_id", "atr_basis", "atr_provenance", "sizing_provenance",
        "cost_model_id", "cost_provenance", "spread_slippage_basis",
        "path_start_basis", "path_end_basis", "row_schema",
        "source_segment_schema", "coverage_provenance",
    )
    numeric_fields = (
        "atr_pct_at_fill", "leverage", "margin_usd", "trading_fees_usd",
        "funding_usd", "latency_cost_usd", "sampling_interval_sec",
        "first_sample_offset_sec", "required_horizon_end_ts",
    )
    if context.get("calculation_mode") == "DECLARED_EXECUTION_RATE_MODEL_V1":
        numeric_fields = tuple(field for field in numeric_fields if field not in
                               {"trading_fees_usd", "funding_usd", "latency_cost_usd"})
    defects = [field for field in text_fields if not isinstance(context.get(field), str)
               or not context[field].strip()]
    for field in numeric_fields:
        value = context.get(field)
        try:
            valid = not isinstance(value, bool) and math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            defects.append(field)
    for field in ("require_fresh_bbo", "require_trade_fields"):
        if not isinstance(context.get(field), bool):
            defects.append(field)
    return sorted(set(defects))


def load_current_policy_candidates(
    report_dir: str | Path, expected_generation: Mapping[str, Any],
    *, policy_cycle_succeeded: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load one fresh exhaustive candidate artifact under its exact sidecar."""
    if not policy_cycle_succeeded:
        raise ValueError("POLICY_CYCLE_NOT_SUCCESSFUL")
    generation, defects = _generation(expected_generation, "EXPECTED")
    if defects:
        raise ValueError(";".join(defects))
    root = Path(report_dir).resolve()
    manifest_path = root / "safe_policy_genome_v3_exhaustive_manifest.json"
    artifact_path = root / "safe_policy_genome_v3_exhaustive.jsonl.gz"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    if not isinstance(manifest, Mapping) or manifest.get("schema") != "safe_policy_exhaustive_manifest_v1":
        raise ValueError("POLICY_MANIFEST_SCHEMA_INVALID")
    expected_identity = {
        "epoch_id": generation["epoch_id"], "source_revision": generation["source_revision"],
        "analyzer_generation_revision": generation["analyzer_revision"],
        "tile_config_signature": generation["tile_config_signature"],
    }
    if any(str(manifest.get(key) or "") != value for key, value in expected_identity.items()):
        raise ValueError("POLICY_MANIFEST_GENERATION_MISMATCH")
    if (manifest.get("artifact") != artifact_path.name or manifest.get("compression") != "gzip"
            or manifest.get("row_schema") != "safe_policy_exhaustive_row_v1"):
        raise ValueError("POLICY_MANIFEST_ARTIFACT_CONTRACT_INVALID")
    compressed = artifact_path.read_bytes()
    digest = hashlib.sha256(compressed).hexdigest()
    if digest != str(manifest.get("sha256") or ""):
        raise ValueError("POLICY_ARTIFACT_SHA256_MISMATCH")
    rows = []
    with gzip.open(io.BytesIO(compressed), "rt", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping) or row.get("schema") != "safe_policy_exhaustive_row_v1":
                raise ValueError(f"POLICY_ARTIFACT_ROW_SCHEMA_INVALID:{number}")
            if any(str(row.get(key) or "") != value for key, value in expected_identity.items()):
                raise ValueError(f"POLICY_ARTIFACT_ROW_GENERATION_MISMATCH:{number}")
            spec = row.get("policy_spec")
            if (not isinstance(spec, Mapping)
                    or not str(row.get("policy_id") or "").strip()
                    or row.get("policy_identity_verified") is not True
                    or row.get("policy_signature") != canonical_hash("v3-policy", spec)):
                raise ValueError(f"POLICY_ARTIFACT_ROW_SIGNATURE_INVALID:{number}")
            rows.append({"policy_id": row.get("policy_id"),
                         "policy_signature": row.get("policy_signature"),
                         "policy_spec": dict(spec)})
    if manifest.get("row_count") != len(rows):
        raise ValueError("POLICY_ARTIFACT_ROW_COUNT_MISMATCH")
    rows.sort(key=lambda item: (str(item["policy_signature"]), str(item["policy_id"])))
    receipt = {
        "schema": "policy_candidate_artifact_receipt_v1",
        "artifact_identity": expected_identity,
        "artifact_verified_identity_fields": sorted(expected_identity),
        "evaluation_generation": generation,
        "generation_binding_basis": "CURRENT_EVALUATION_CONTEXT_NOT_COLLECTED_ARTIFACT_IDENTITY",
        "candidate_count": len(rows), "candidates_sha256": _sha(rows),
        "source_basis": "IN_MEMORY_CURRENT_GENERATION",
        "original_artifact_provenance": {
            "manifest_path": manifest_path.name,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "artifact_path": artifact_path.name, "artifact_sha256": digest,
            "artifact_size_bytes": len(compressed), "row_count": len(rows),
        },
    }
    return rows, receipt


def _accepted_fill_completion_ts(entry_receipt: Mapping[str, Any]) -> float | None:
    """Mirror the terminal evaluator's accepted-fill timestamp derivation."""
    try:
        top_level = float(entry_receipt.get("trigger_bucket_ts"))
        filled_qty = float(entry_receipt.get("filled_qty"))
        if not math.isfinite(top_level) or not math.isfinite(filled_qty) or filled_qty <= 0:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    events = []
    for attempt in entry_receipt.get("quantity_attempts") or []:
        if not isinstance(attempt, Mapping) or attempt.get("accepted") is not True:
            continue
        try:
            timestamp = float(attempt.get("trigger_bucket_ts"))
            price = float(attempt.get("execution_price"))
            quantity = float(attempt.get("rounded_executable_quantity"))
        except (TypeError, ValueError, OverflowError):
            continue
        if (not all(math.isfinite(value) for value in (timestamp, price, quantity))
                or int(timestamp) != timestamp or price <= 0 or quantity <= 0):
            continue
        events.append((timestamp, quantity))
    if events and abs(sum(quantity for _timestamp, quantity in events) - filled_qty) <= 1e-12:
        return max(timestamp for timestamp, _quantity in events)
    return top_level


def build_composite_policy_identity(
    entry_result: Mapping[str, Any], candidate: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Bind the observed baseline entry to only the candidate exit treatment."""
    baseline_spec = entry_result.get("baseline_spec")
    entry_receipt = entry_result.get("conservative_receipt")
    candidate_spec = candidate.get("policy_spec")
    if not all(isinstance(item, Mapping) for item in (baseline_spec, entry_receipt, candidate_spec)):
        raise ValueError("COMPOSITE_POLICY_INPUT_MISSING")
    unsigned_baseline = {key: value for key, value in baseline_spec.items() if key != "policy_signature"}
    baseline_signature = str(entry_result.get("policy_signature") or "")
    if (baseline_signature != str(baseline_spec.get("policy_signature") or "")
            or baseline_signature != canonical_hash("entry-baseline", unsigned_baseline)):
        raise ValueError("ENTRY_BASELINE_SIGNATURE_INVALID")
    candidate_signature = str(candidate.get("policy_signature") or "")
    if candidate_signature != canonical_hash("v3-policy", candidate_spec):
        raise ValueError("SOURCE_CANDIDATE_SIGNATURE_INVALID")
    exit_projection = {
        "loss_protection": dict(candidate_spec.get("loss_protection") or {}),
        "profit_protection": dict(candidate_spec.get("profit_protection") or {}),
    }
    composite_spec = {
        "entry": {
            "entry_policy_id": entry_result.get("baseline_id"),
            "entry_baseline_signature": baseline_signature,
            "baseline_spec": dict(baseline_spec),
        },
        "fill": {
            "execution_world": "CONSERVATIVE_BBO_DEPTH_V1",
            "evaluator_version": entry_receipt.get("evaluator_version"),
            "entry_receipt_schema": entry_receipt.get("schema"),
        },
        **exit_projection,
        "portfolio": {"concurrency_cap": 1, "size_scale": 1,
                      "evaluation_scope": "ONE_BASELINE_FILLED_POSITION"},
    }
    identities = {
        "composite_policy_signature": canonical_hash("v3-policy", composite_spec),
        "entry_baseline_signature": baseline_signature,
        "exit_policy_signature": canonical_hash("candidate-exit", exit_projection),
        "source_candidate_policy_signature": candidate_signature,
        "source_candidate_portfolio_signature": canonical_hash(
            "source-candidate-portfolio", candidate_spec.get("portfolio") or {}),
    }
    return composite_spec, identities


def _load_paths(root: Path, receipt: Mapping[str, Any], generation: Mapping[str, str]) -> tuple[list[dict[str, Any]], list[bytes], list[dict[str, Any]], list[str]]:
    loaded, blockers = [], []
    provenance = receipt.get("market_evidence_provenance")
    if not isinstance(provenance, list):
        return [], [], [], ["MARKET_EVIDENCE_PROVENANCE_MISSING"]
    authority = root.resolve()
    for source in provenance:
        if not isinstance(source, Mapping) or source.get("status") != "VERIFIED":
            continue
        digest = str(source.get("sha256") or "").lower()
        relative = source.get("relative_path")
        if len(digest) != 64 or not isinstance(relative, str):
            blockers.append("SOURCE_SEGMENT_REFERENCE_INVALID")
            continue
        candidate = (authority / relative).resolve()
        try:
            candidate.relative_to(authority)
        except ValueError:
            blockers.append("SOURCE_SEGMENT_PATH_OUTSIDE_CANONICAL_ROOT")
            continue
        try:
            payload = candidate.read_bytes()
        except OSError:
            blockers.append("SOURCE_SEGMENT_PAYLOAD_MISSING")
            continue
        if hashlib.sha256(payload).hexdigest() != digest:
            blockers.append("SOURCE_SEGMENT_SHA256_MISMATCH")
            continue
        try:
            envelope = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError):
            blockers.append("SOURCE_SEGMENT_PAYLOAD_INVALID_JSON")
            continue
        rows = envelope.get("rows") if isinstance(envelope, Mapping) else None
        if not isinstance(envelope, Mapping) or envelope.get("schema") != "market_segment_v3" or not isinstance(rows, list):
            blockers.append("SOURCE_SEGMENT_PAYLOAD_SCHEMA_INVALID")
            continue
        if not all(isinstance(row, Mapping) for row in rows):
            blockers.append("SOURCE_SEGMENT_PAYLOAD_ROWS_INVALID")
            continue
        try:
            first_ts = min(float(row["bucket_ts"]) for row in rows)
        except (KeyError, TypeError, ValueError, OverflowError):
            blockers.append("SOURCE_SEGMENT_TIMESTAMP_INVALID")
            continue
        loaded.append((first_ts, digest, payload, [dict(row) for row in rows], source))
    loaded.sort(key=lambda item: (item[0], item[1]))
    payloads = [item[2] for item in loaded]
    rows = [row for item in loaded for row in item[3]]
    source_receipts = []
    for item in loaded:
        unsigned = {
        "schema": "market_segment_v3", "sha256": item[1],
        "verification_status": "CHECKSUM_VERIFIED",
        "verifier_version": "conservative-shadow-report-v1", "generation": dict(generation),
        "source_relative_path": item[4].get("relative_path"),
        "source_segment_record_id": item[4].get("segment_record_id"),
        }
        source_receipts.append({**unsigned, "receipt_sha256": _sha(unsigned)})
    if not loaded:
        blockers.append("SOURCE_SEGMENT_PAYLOADS_MISSING")
    return rows, payloads, source_receipts, blockers


def build_conservative_shadow_report(
    canonical_root: str | Path, *, expected_generation: Mapping[str, Any],
    baseline_report: Mapping[str, Any], policy_candidates: Sequence[Mapping[str, Any]],
    policy_artifact_receipt: Mapping[str, Any], research_model: Mapping[str, Any] | None = None,
    result_sink=None, max_diagnostic_results: int = 100,
) -> dict[str, Any]:
    if type(max_diagnostic_results) is not int or not 0 <= max_diagnostic_results <= 1000:
        raise ValueError("SHADOW_DIAGNOSTIC_LIMIT_INVALID")
    if result_sink is not None and not callable(result_sink):
        raise ValueError("SHADOW_RESULT_SINK_INVALID")
    baseline_report = baseline_report if isinstance(baseline_report, Mapping) else {}
    policy_artifact_receipt = (
        policy_artifact_receipt if isinstance(policy_artifact_receipt, Mapping) else {}
    )
    generation, blockers = _generation(expected_generation, "EXPECTED")
    baseline_generation, defects = _generation(
        baseline_report.get("generation"),
        "BASELINE",
    ); blockers.extend(defects)
    artifact_generation, defects = _generation(
        policy_artifact_receipt.get("evaluation_generation"),
        "POLICY_ARTIFACT",
    ); blockers.extend(defects)
    if baseline_report.get("schema") != "entry_baseline_same_opportunity_replay_v1":
        blockers.append("BASELINE_REPORT_SCHEMA_INVALID")
    if policy_artifact_receipt.get("schema") != "policy_candidate_artifact_receipt_v1":
        blockers.append("POLICY_ARTIFACT_RECEIPT_SCHEMA_INVALID")
    expected_artifact_identity = {
        "epoch_id": generation.get("epoch_id"),
        "source_revision": generation.get("source_revision"),
        "analyzer_generation_revision": generation.get("analyzer_revision"),
        "tile_config_signature": generation.get("tile_config_signature"),
    }
    if (policy_artifact_receipt.get("artifact_identity") != expected_artifact_identity
            or policy_artifact_receipt.get("artifact_verified_identity_fields")
            != sorted(expected_artifact_identity)
            or policy_artifact_receipt.get("generation_binding_basis")
            != "CURRENT_EVALUATION_CONTEXT_NOT_COLLECTED_ARTIFACT_IDENTITY"):
        blockers.append("POLICY_ARTIFACT_IDENTITY_BINDING_INVALID")
    if not blockers and (generation != baseline_generation or generation != artifact_generation):
        blockers.append("INPUT_GENERATION_MISMATCH")
    candidates = [dict(item) for item in policy_candidates or [] if isinstance(item, Mapping)]
    candidates.sort(key=lambda item: (str(item.get("policy_signature") or ""), str(item.get("policy_id") or "")))
    if len(candidates) != len(policy_candidates or []):
        blockers.append("POLICY_CANDIDATE_NOT_OBJECT")
    if policy_artifact_receipt.get("candidate_count") != len(candidates):
        blockers.append("POLICY_ARTIFACT_CANDIDATE_COUNT_MISMATCH")
    if str(policy_artifact_receipt.get("candidates_sha256") or "") != _sha(candidates):
        blockers.append("POLICY_ARTIFACT_CANDIDATES_SHA256_MISMATCH")
    source_basis = policy_artifact_receipt.get("source_basis")
    if source_basis != "IN_MEMORY_CURRENT_GENERATION":
        relative = policy_artifact_receipt.get("source_path")
        expected_sha = str(policy_artifact_receipt.get("artifact_sha256") or "").lower()
        if not isinstance(relative, str):
            blockers.append("POLICY_ARTIFACT_PATH_MISSING")
        else:
            root = Path(canonical_root).resolve(); path = (root / relative).resolve()
            try: path.relative_to(root)
            except ValueError: blockers.append("POLICY_ARTIFACT_PATH_OUTSIDE_CANONICAL_ROOT")
            else:
                try: payload = path.read_bytes()
                except OSError: blockers.append("POLICY_ARTIFACT_MISSING")
                else:
                    if hashlib.sha256(payload).hexdigest() != expected_sha:
                        blockers.append("POLICY_ARTIFACT_SHA256_MISMATCH")
    for candidate in candidates:
        spec = candidate.get("policy_spec")
        if (not isinstance(spec, Mapping)
                or candidate.get("policy_signature") != canonical_hash("v3-policy", spec)):
            blockers.append("POLICY_CANDIDATE_SIGNATURE_INVALID")
    if len({str(item.get("policy_signature") or "") for item in candidates}) != len(candidates):
        blockers.append("DUPLICATE_POLICY_CANDIDATE_SIGNATURE")

    if isinstance(research_model, Mapping) and research_model.get("schema") == "declared_shadow_model_v1":
        from research.declared_shadow_model import build_declared_research_model
        try:
            research_model = build_declared_research_model(
                research_model, baseline_report=baseline_report, policy_candidates=candidates,
                expected_generation=generation)
        except ValueError as exc:
            return _unknown(generation, [str(exc)])
    lazy_contexts = isinstance(research_model, Mapping) and research_model.get("context_binding_mode") == "PER_BASELINE_LAZY_COMPOSITE"
    model_contexts: dict[tuple, Mapping[str, Any]] = {}
    if research_model is None:
        model_blocker = "RESEARCH_MODEL_MISSING"
    else:
        model_blocker = None
        unsigned = {key: value for key, value in research_model.items() if key != "signature"}
        if (research_model.get("schema") != "conservative_shadow_research_model_v1"
                or research_model.get("generation") != generation
                or research_model.get("signature") != stable_hash("conservative-shadow-research-model", unsigned)
                or not str(research_model.get("model_id") or "")
                or not str(research_model.get("provenance") or "")):
            model_blocker = "RESEARCH_MODEL_INVALID"
        contexts = research_model.get("contexts") if isinstance(research_model.get("contexts"), list) else []
        for context in contexts:
            if not isinstance(context, Mapping):
                model_blocker = "RESEARCH_MODEL_CONTEXT_INVALID"; continue
            key_fields = ("episode_id", "opportunity_id", "baseline_id")
            if not lazy_contexts: key_fields += ("composite_policy_signature",)
            key = tuple(str(context.get(field) or "") for field in key_fields)
            if not all(key) or key in model_contexts:
                model_blocker = "RESEARCH_MODEL_CONTEXT_IDENTITY_INVALID"; continue
            model_contexts[key] = context
    if blockers:
        return _unknown(generation, blockers)

    receipts = baseline_report.get("episode_receipts") if isinstance(baseline_report.get("episode_receipts"), list) else []
    episode_counts = Counter(str(item.get("episode_id") or "") for item in receipts if isinstance(item, Mapping))
    duplicate_episodes = sorted(key for key, count in episode_counts.items() if key and count > 1)
    eligible_entries = []
    for episode in receipts:
        if not isinstance(episode, Mapping) or str(episode.get("episode_id") or "") in duplicate_episodes:
            continue
        for entry in episode.get("results") or []:
            if not isinstance(entry, Mapping):
                continue
            entry_receipt = entry.get("conservative_receipt")
            if (isinstance(entry_receipt, Mapping) and entry.get("supported") is True
                    and entry.get("outcome_state") in {"FULL_FILL", "PARTIAL_FILL"}):
                eligible_entries.append((episode, entry))
    if model_blocker == "RESEARCH_MODEL_MISSING":
        replay_count = len(eligible_entries) * len(candidates)
        diagnostic_limit = 100
        diagnostic_results = []
        for episode, entry in eligible_entries:
            for candidate in candidates:
                if len(diagnostic_results) >= diagnostic_limit:
                    break
                diagnostic_results.append({
                    "episode_id": str(episode.get("episode_id") or ""),
                    "opportunity_id": str(episode.get("opportunity_id") or ""),
                    "baseline_id": str(entry.get("baseline_id") or ""),
                    "policy_id": candidate.get("policy_id"),
                    "source_candidate_policy_signature": candidate.get("policy_signature"),
                    "status": "UNKNOWN", "blockers": [model_blocker], "net_pnl_usd": None,
                })
            if len(diagnostic_results) >= diagnostic_limit:
                break
        return {
            "schema": SCHEMA, "status": "BUILT_INCOMPLETE", "generation": generation,
            "blockers": sorted(set([model_blocker] + (["DUPLICATE_BASELINE_EPISODE"] if duplicate_episodes else []))),
            "independent_episode_count": len([key for key in episode_counts if key]) - len(duplicate_episodes),
            "duplicate_episode_ids": duplicate_episodes, "candidate_policy_count": len(candidates),
            "candidate_replay_count": replay_count, "complete_replay_count": 0,
            "unknown_replay_count": replay_count,
            "reason_counts": {model_blocker: replay_count}, "results": diagnostic_results,
            "results_total": replay_count, "results_truncated": replay_count > len(diagnostic_results),
            "profitability_supported": False, "ranking_eligible": False,
            "live_qualification": False,
            "source_basis": "CURRENT_IN_MEMORY_INPUTS_MODEL_ABSENT_NO_SEGMENT_IO",
        }
    reason_counts: Counter[str] = Counter()
    results = []
    streamed_count = 0
    def record_result(row):
        nonlocal streamed_count
        if result_sink is not None:
            result_sink(row)
            streamed_count += 1
        if len(results) < max_diagnostic_results:
            results.append(row)
    complete = unknown = replay_count = 0
    terminal_evaluated_count = 0
    evaluated_composite_signatures: set[str] = set()
    for episode in sorted((item for item in receipts if isinstance(item, Mapping)),
                          key=lambda item: (str(item.get("opportunity_id") or ""), str(item.get("episode_id") or ""))):
        episode_id, opportunity_id = str(episode.get("episode_id") or ""), str(episode.get("opportunity_id") or "")
        if episode_id in duplicate_episodes:
            reason_counts["DUPLICATE_BASELINE_EPISODE"] += 1
            continue
        loaded_paths = None
        for entry in sorted((item for item in episode.get("results") or [] if isinstance(item, Mapping)),
                            key=lambda item: str(item.get("baseline_id") or "")):
            entry_receipt = entry.get("conservative_receipt")
            if not isinstance(entry_receipt, Mapping) or entry.get("supported") is not True \
                    or entry.get("outcome_state") not in {"FULL_FILL", "PARTIAL_FILL"}:
                continue
            lazy_context = model_contexts.get((episode_id, opportunity_id, str(entry.get("baseline_id") or ""))) if lazy_contexts else None
            lazy_blockers = []
            if lazy_contexts:
                if model_blocker: lazy_blockers.append(model_blocker)
                if lazy_context is None: lazy_blockers.append("RESEARCH_MODEL_CONTEXT_MISSING")
                elif lazy_context.get("input_blockers"): lazy_blockers.extend(lazy_context["input_blockers"])
                else: lazy_blockers.extend(f"RESEARCH_MODEL_CONTEXT_FIELD_INVALID:{field}" for field in _context_defects(lazy_context))
            if lazy_blockers:
                # Context absence invalidates the whole Cartesian fanout, not
                # one sampled policy. Count exactly without hashing N policies
                # or loading paths; retain bounded examples and one range row.
                amount = len(candidates)
                replay_count += amount; unknown += amount
                for reason in set(lazy_blockers): reason_counts[reason] += amount
                if result_sink is not None:
                    result_sink({"schema": "shadow_unknown_candidate_range_v1", "episode_id": episode_id,
                                 "opportunity_id": opportunity_id, "baseline_id": entry.get("baseline_id"),
                                 "candidate_count": amount, "candidate_artifact_sha256": policy_artifact_receipt.get("candidates_sha256"),
                                 "status": "UNKNOWN", "blockers": sorted(set(lazy_blockers))})
                for candidate in candidates[:max(0, max_diagnostic_results-len(results))]:
                    results.append({"episode_id": episode_id, "opportunity_id": opportunity_id,
                                    "baseline_id": entry.get("baseline_id"), "policy_id": candidate.get("policy_id"),
                                    "source_candidate_policy_signature": candidate.get("policy_signature"),
                                    "status": "UNKNOWN", "blockers": sorted(set(lazy_blockers)), "net_pnl_usd": None})
                continue
            if loaded_paths is None:
                loaded_paths = _load_paths(Path(canonical_root), episode, generation)
            episode_path_rows, payloads, source_receipts, path_blockers = loaded_paths
            for candidate in candidates:
                replay_count += 1
                try:
                    composite_spec, policy_identity = build_composite_policy_identity(entry, candidate)
                    policy_signature = policy_identity["composite_policy_signature"]
                    evaluated_composite_signatures.add(policy_signature)
                    composite_blocker = None
                except ValueError as exc:
                    composite_spec, policy_identity = {}, {}
                    policy_signature = ""
                    composite_blocker = str(exc)
                key = (episode_id, opportunity_id, str(entry.get("baseline_id") or ""), policy_signature)
                context = lazy_context if lazy_contexts else model_contexts.get(key)
                local_blockers = list(path_blockers)
                if composite_blocker: local_blockers.append(composite_blocker)
                if model_blocker: local_blockers.append(model_blocker)
                if context is None: local_blockers.append("RESEARCH_MODEL_CONTEXT_MISSING")
                elif context.get("input_blockers"):
                    local_blockers.extend(str(reason) for reason in context["input_blockers"])
                elif defects := _context_defects(context):
                    local_blockers.extend(
                        f"RESEARCH_MODEL_CONTEXT_FIELD_INVALID:{field}" for field in defects
                    )
                if local_blockers:
                    reason_counts.update(local_blockers); unknown += 1
                    record_result({"episode_id": episode_id, "opportunity_id": opportunity_id,
                                    "baseline_id": key[2], "policy_signature": policy_signature,
                                    **policy_identity,
                                    "source_candidate_policy_id": candidate.get("policy_id"),
                                    "evaluated_scope": "ENTRY_PLUS_SINGLE_POSITION_EXIT",
                                    "portfolio_competition_status": "NOT_SIMULATED",
                                    "status": "UNKNOWN", "blockers": sorted(set(local_blockers)),
                                    "net_pnl_usd": None})
                    continue
                entry_sha = _sha(entry_receipt)
                try:
                    fill_ts = _accepted_fill_completion_ts(entry_receipt)
                    first_offset = float(context.get("first_sample_offset_sec"))
                    horizon = float(context.get("required_horizon_end_ts"))
                    path_rows = [row for row in episode_path_rows
                                 if fill_ts + first_offset <= float(row.get("bucket_ts")) <= horizon]
                except (TypeError, ValueError, OverflowError):
                    path_rows = []
                path_sha = _sha(path_rows)
                bindings = {"entry_receipt_sha256": entry_sha, "future_path_sha256": path_sha,
                            "policy_signature": policy_signature, "generation": generation}
                position = _signed("conservative-shadow-position-context", {
                    "schema": "conservative_shadow_position_context_v1",
                    "position_context_id": context.get("position_context_id"),
                    "atr_pct_at_fill": context.get("atr_pct_at_fill"),
                    "atr_basis": context.get("atr_basis"), "atr_provenance": context.get("atr_provenance"),
                    "leverage": context.get("leverage"), "margin_usd": context.get("margin_usd"),
                    "sizing_provenance": context.get("sizing_provenance"), **bindings,
                })
                costs = _signed("conservative-shadow-cost-model", {
                    "schema": "conservative_shadow_cost_model_v1", "cost_model_id": context.get("cost_model_id"),
                    "trading_fees_usd": context.get("trading_fees_usd"), "funding_usd": context.get("funding_usd"),
                    "latency_cost_usd": context.get("latency_cost_usd"), "cost_provenance": context.get("cost_provenance"),
                    "spread_slippage_basis": context.get("spread_slippage_basis"), **bindings,
                    **({"calculation_mode": context["calculation_mode"],
                        "declared_contract": context.get("declared_contract")}
                       if context.get("calculation_mode") == "DECLARED_EXECUTION_RATE_MODEL_V1" else {}),
                })
                coverage = _signed("shadow-path-coverage-policy", {
                    "schema": "shadow_path_coverage_policy_v1",
                    "sampling_interval_sec": context.get("sampling_interval_sec"),
                    "first_sample_offset_sec": context.get("first_sample_offset_sec"),
                    "require_fresh_bbo": context.get("require_fresh_bbo"),
                    "require_trade_fields": context.get("require_trade_fields"),
                    "path_start_basis": context.get("path_start_basis"),
                    "path_end_basis": context.get("path_end_basis"),
                    "row_schema": context.get("row_schema"), "source_segment_schema": context.get("source_segment_schema"),
                    "coverage_provenance": context.get("coverage_provenance"), **bindings,
                })
                terminal_evaluated_count += 1
                terminal = evaluate_shadow_terminal(
                    generation=generation, entry_receipt=entry_receipt, entry_receipt_sha256=entry_sha,
                    future_path_rows=path_rows, future_path_sha256=path_sha,
                    required_horizon_end_ts=context.get("required_horizon_end_ts"),
                    policy_spec=composite_spec, policy_signature=policy_signature,
                    position_context=position, cost_model=costs, coverage_policy=coverage,
                    source_segment_receipts=source_receipts, source_segment_payloads=payloads,
                )
                status = terminal.get("status")
                complete += status == "COMPLETE"; unknown += status != "COMPLETE"
                reason_counts.update(terminal.get("blockers") or [])
                record_result({"episode_id": episode_id, "opportunity_id": opportunity_id,
                                "baseline_id": key[2], "policy_id": candidate.get("policy_id"),
                                "policy_signature": policy_signature, **policy_identity,
                                "source_candidate_policy_id": candidate.get("policy_id"),
                                "evaluated_scope": "ENTRY_PLUS_SINGLE_POSITION_EXIT",
                                "portfolio_competition_status": "NOT_SIMULATED",
                                "terminal": terminal,
                                "status": status, "net_pnl_usd": terminal.get("net_pnl_usd")})
    truncated = len(results) < replay_count
    publication_blockers = (["DUPLICATE_BASELINE_EPISODE"] if duplicate_episodes else [])
    if truncated:
        publication_blockers.append("RESULT_STREAM_CONSUMER_NOT_BOUND" if result_sink is not None
                                    else "RESULTS_TRUNCATED_WITHOUT_STREAM")
    return {
        "schema": SCHEMA,
        "status": "BUILT" if replay_count and unknown == 0 and not duplicate_episodes and not truncated
        else "BUILT_INCOMPLETE",
        "generation": generation, "blockers": publication_blockers,
        "independent_episode_count": len([key for key in episode_counts if key]) - len(duplicate_episodes),
        "duplicate_episode_ids": duplicate_episodes, "candidate_policy_count": len(candidates),
        "candidate_replay_count": replay_count, "complete_replay_count": complete,
        "candidate_replay_count_basis": "FULL_CANDIDATE_CARTESIAN_COUNT_WITH_ARITHMETIC_UNKNOWN_RANGES",
        "terminal_evaluated_count": terminal_evaluated_count,
        "evaluated_composite_policy_count": len(evaluated_composite_signatures),
        "unknown_replay_count": unknown, "reason_counts": dict(sorted(reason_counts.items())),
        "results": results, "results_total": replay_count, "results_truncated": len(results) < replay_count,
        "individual_results_streamed": streamed_count,
        "complete_result_stream_status": "CALLER_SINK_USED" if result_sink is not None else "NOT_REQUESTED",
        "profitability_supported": complete > 0 and unknown == 0 and not duplicate_episodes and not truncated,
        "ranking_eligible": False, "live_qualification": False,
        "source_basis": "CURRENT_IN_MEMORY_INPUTS_AND_HASH_VERIFIED_CANONICAL_SEGMENTS",
        "evaluation_scope": "ENTRY_PLUS_SINGLE_POSITION_EXIT",
        "portfolio_competition_status": "NOT_SIMULATED",
        "research_model_provenance": research_model.get("provenance") if isinstance(research_model, Mapping) else None,
        "declared_contract_sha256": research_model.get("declared_contract_sha256") if isinstance(research_model, Mapping) else None,
        "declared_contract": research_model.get("declared_contract") if isinstance(research_model, Mapping) else None,
    }
