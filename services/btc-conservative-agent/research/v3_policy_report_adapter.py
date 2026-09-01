"""Canonical V3.1 adapter for legacy-named policy-readiness artifacts.

The dashboard still exposes stable report filenames used by older clients.
Once a V3.1 opportunity ledger exists those filenames must describe the V3.1
cohort, never fall back to an empty retired V2.2 event file.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from research.best_policy_research import (
    QUALIFICATION_GATE_SCHEMA,
    REQUIRED_QUALIFICATION_GATES,
    qualification_gate_details,
)


_CONSERVATIVE_INTENT_FIELDS = (
    "schema", "ledger", "record_id", "event_id", "episode_id", "epoch_id",
    "opportunity_id", "policy_epoch_id", "policy_id", "policy_signature",
    "schedule_id", "tape_id", "research_lane", "intent_kind",
    "execution_basis", "requested_qty", "executed_direction", "chase_schedule",
)


def _snapshot_size(path: Path) -> int:
    try:
        return max(0, int(path.stat().st_size))
    except OSError:
        return 0


def _iter_jsonl(path: Path, *, byte_limit: int | None = None):
    """Yield rows only through one pre-captured immutable size boundary."""
    limit = _snapshot_size(path) if byte_limit is None else max(0, int(byte_limit))
    if limit <= 0:
        return
    try:
        handle = path.open("rb")
    except OSError:
        return
    with handle:
        remaining = limit
        while remaining > 0:
            raw = handle.readline(remaining)
            if not raw:
                break
            remaining -= len(raw)
            # A row appended beyond the captured boundary, or an incomplete
            # concurrent write, belongs to the next analyzer generation.
            if not raw.endswith(b"\n"):
                break
            try:
                value = json.loads(raw.decode("utf-8", errors="replace"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                yield value


def _jsonl(path: Path) -> list[dict]:
    return list(_iter_jsonl(path))


def has_v3_evidence(data_dir=".") -> bool:
    return (Path(data_dir) / "v3" / "ledgers" / "opportunity.jsonl").is_file()


def load_v3_cycle_snapshot(data_dir=".") -> dict:
    root = Path(data_dir)
    ledger_root = root / "v3" / "ledgers"
    names = ("opportunity", "decision", "order_intent", "execution", "lifecycle", "market_segment")
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    latest: dict = {}
    epoch_id = None
    signatures: set[str] = set()
    policy_epochs: set[str] = set()
    for name in names:
        count = 0
        path = ledger_root / f"{name}.jsonl"
        boundary = _snapshot_size(path)
        for row in _iter_jsonl(path, byte_limit=boundary):
            count += 1
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            if name == "opportunity" and (
                not latest
                or float(row.get("signal_ts") or 0) > float(latest.get("signal_ts") or 0)
            ):
                latest = row
            elif name == "decision" and (not epoch_id or row.get("epoch_id") == epoch_id):
                if row.get("policy_signature"):
                    signatures.add(str(row["policy_signature"]))
                if row.get("policy_epoch_id"):
                    policy_epochs.add(str(row["policy_epoch_id"]))
        counts[name] = count
        if name == "opportunity":
            epoch_id = str(latest.get("epoch_id") or "") or None
    sorted_signatures = sorted(signatures)
    sorted_policy_epochs = sorted(policy_epochs)
    return {
        "schema": "policy_cycle_snapshot_v3_1",
        "snapshot_id": "policy-v3-snapshot-" + digest.hexdigest()[:24],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_file": "v3/ledgers/*.jsonl",
        "source_read_mode": "STREAMED_JSONL_V1",
        "collector_version": "collector_v3.1",
        "row_count": counts["opportunity"],
        "ledger_counts": counts,
        "last_event_id": latest.get("episode_id"),
        "last_signal_ts": latest.get("signal_ts"),
        "epoch_id": epoch_id,
        "policy_epoch_id": sorted_policy_epochs[0] if len(sorted_policy_epochs) == 1 else None,
        "policy_epoch_ids": sorted_policy_epochs,
        "policy_signature": sorted_signatures[0] if len(sorted_signatures) == 1 else None,
        "policy_signatures": sorted_signatures,
    }


def load_v3_order_intents(data_dir=".", *, epoch_id: str | None = None) -> tuple[dict, ...]:
    """Return immutable V3.1 order-intent rows for one signed epoch."""
    path = Path(data_dir) / "v3" / "ledgers" / "order_intent.jsonl"
    boundary = _snapshot_size(path)
    wanted = None if epoch_id is None else str(epoch_id)
    projected = []
    for row in _iter_jsonl(path, byte_limit=boundary):
        if wanted is not None and str(row.get("epoch_id") or "") != wanted:
            continue
        projected.append({key: row[key] for key in _CONSERVATIVE_INTENT_FIELDS if key in row})
    # json.loads already returns a fresh object per line. A JSON dump/load
    # deep-copy doubled peak memory for the 226 MB canonical ledger and was
    # the native-fault site; returning the parsed rows preserves exact values
    # and durable line order without redundant serialization.
    return tuple(projected)


def load_or_build_genome(data_dir=".", report_dir=".") -> dict:
    from research.research_v3_report import build_safe_policy_genome_v3_report

    return build_safe_policy_genome_v3_report(data_dir=data_dir, report_dir=report_dir)


def candidate_from_genome(genome: dict, cycle_snapshot: dict, microstructure_evidence=None) -> dict:
    collection = genome.get("collection") or {}
    ranking = genome.get("safe_policy_ranking") or {}
    screen = genome.get("candidate_screen") or {}
    winner = genome.get("number_one_strategy")
    winner_declared_qualified = bool(
        winner
        and genome.get("qualification") not in (None, "NO_SAFE_QUALIFIED_POLICY")
    )
    required = REQUIRED_QUALIFICATION_GATES
    winner_gates = (winner or {}).get("gates") or {}
    validation = (winner or {}).get("validation") or {}
    episode_count = int(collection.get("independent_opportunities") or 0)
    execution_count = int(collection.get("execution_rows") or 0)
    segment_count = int(collection.get("market_segments") or 0)
    integrity_passed = bool((genome.get("integrity") or {}).get("passed"))
    microstructure = microstructure_evidence if isinstance(microstructure_evidence, dict) else {}
    evaluator = microstructure.get("conservative_evaluator") or {}
    cell_support = (
        microstructure.get("phase7_support_qualification")
        or (evaluator.get("phase7_support_qualification") if isinstance(evaluator, dict) else None)
        or {}
    )
    gates = {name: bool(winner_gates.get(name)) for name in required}
    # The genome validation schema names this mandatory safety gate with its
    # explicit pass suffix.  Preserve that evidence instead of silently
    # converting a proven same-cohort neighborhood to false.
    gates["parameter_neighborhood_stability"] = bool(
        winner_gates.get("neighborhood_stability_pass")
    )
    gates.update({
        "purged_walk_forward_validation": bool(winner_gates.get("purged_walk_forward_pass")),
        "sealed_holdout_receipt": bool(winner_gates.get("sealed_holdout_pass")),
        "measured_execution_costs": bool(winner_gates.get("measured_costs_pass")),
        "liquidation_buffer": bool(winner_gates.get("liquidation_buffer_pass")),
        "detailed_regime_support": bool(
            cell_support.get("schema") == "phase7_regime_support_qualification_v2"
            and cell_support.get("qualification_allowed") is True
            and (cell_support.get("gates") or {}).get("every_eligible_regime_direction_cell_supported") is True
        ),
        "baseline_replay_coverage": bool(winner_gates.get("baseline_replay_coverage_pass")),
    })
    purged = validation.get("purged_walk_forward") or {}
    sealed = validation.get("sealed_holdout") or {}
    measured = validation.get("measured_cost_evidence") or {}
    liquidation = validation.get("liquidation_buffer") or {}
    gate_evidence = {
        "purged_walk_forward_validation": {
            "evidence": purged.get("schema"),
            "receipt_id": purged.get("receipt_id"),
            "blocker": next(iter(purged.get("blockers") or []), None),
            "source": "winner.validation.purged_walk_forward",
        },
        "sealed_holdout_receipt": {
            "evidence": sealed.get("schema"),
            "receipt_id": sealed.get("receipt_id"),
            "blocker": next(iter(sealed.get("blockers") or []), None),
            "source": "winner.validation.sealed_holdout",
        },
        "measured_execution_costs": {
            "evidence": measured.get("schema"),
            "blocker": next(iter(measured.get("defects") or []), None),
            "source": "winner.validation.measured_cost_evidence",
        },
        "liquidation_buffer": {
            "evidence": liquidation.get("schema"),
            "receipt_id": liquidation.get("receipt_id"),
            "blocker": next(iter(liquidation.get("blockers") or []), None),
            "source": "winner.validation.liquidation_buffer",
        },
        "detailed_regime_support": {
            "evidence": cell_support.get("schema"),
            "raw_independent_decision_n": cell_support.get("raw_independent_decision_n"),
            "cluster_adjusted_effective_n": cell_support.get("cluster_adjusted_effective_n"),
            "eligible_cells": cell_support.get("eligible_regime_direction_cells"),
            "blocker": next(iter(cell_support.get("reason_codes") or ["CANONICAL_CELL_SUPPORT_RECEIPT_MISSING"]), None),
            "source": "conservative_microstructure_evidence.phase7_support_qualification",
        },
        "baseline_replay_coverage": {
            "evidence": ((winner or {}).get("baseline_replay_coverage") or {}).get("schema"),
            "receipt_id": ((winner or {}).get("baseline_replay_coverage") or {}).get("receipt_id"),
            "blocker": next(iter(
                ((winner or {}).get("baseline_replay_coverage") or {}).get("blockers") or
                ["BASELINE_REPLAY_COVERAGE_NOT_PROVEN"]
            ), None),
            "source": "winner.baseline_replay_coverage",
        },
    }
    # Cohort-level gates are truthful before a winner exists. Candidate-level
    # performance gates remain false until the sealed ranking supplies them.
    gates.update({
        "chronological_untouched_oos": episode_count >= 50,
        "minimum_independent_episodes": episode_count >= 100,
        "conservative_execution": execution_count > 0 and segment_count > 0,
        "no_data_integrity_defects": integrity_passed,
    })
    blockers = list(genome.get("blockers") or [])
    if execution_count == 0:
        blockers.append("V3_EXECUTION_PATHS_NOT_MATURED")
    if segment_count == 0:
        blockers.append("V3_MARKET_SEGMENTS_NOT_MATURED")
    if episode_count < 100:
        blockers.append("V3_MINIMUM_INDEPENDENT_EPISODES_NOT_MET")
    blockers.extend(f"QUALIFICATION_GATE_FAILED:{name}" for name, passed in gates.items() if not passed)
    blockers = sorted(set(blockers))
    qualified = bool(
        winner_declared_qualified
        and all(gates.values())
        and not blockers
    )
    identities = collection.get("effective_paper_execution_identities") or []
    identity = identities[0] if len(identities) == 1 else {}
    descriptive = (screen.get("descriptive_top_100") or [None])[0]
    evidence = {
        "current_events": int(collection.get("independent_opportunities") or 0),
        "eligible_events": int(collection.get("execution_rows") or 0),
        "excluded_events": max(0, int(collection.get("independent_opportunities") or 0) - int(collection.get("execution_rows") or 0)),
        "independent_episodes": int(collection.get("independent_opportunities") or 0),
        "train_episodes": int((screen.get("split") or {}).get("train") or 0),
        "oos_episodes": int((screen.get("split") or {}).get("oos") or 0),
        "qualified_oos_episodes": int((winner or {}).get("oos_episodes") or 0),
        "terminal_lifecycles": int(collection.get("terminal_lifecycles") or 0),
        "provisional_lifecycles": int(collection.get("provisional_lifecycles") or 0),
        "market_segments": int(collection.get("market_segments") or 0),
        "order_intents": int((collection.get("ledger_counts") or {}).get("order_intent") or 0),
    }
    return {
        "schema": "policy_candidate_oos_v3_1_adapter_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "epoch_id": genome.get("epoch_id"),
        "policy_epoch_id": identity.get("policy_epoch_id"),
        "evidence_policy_signature": identity.get("policy_signature"),
        "cycle_snapshot": cycle_snapshot,
        "conservative_microstructure_evidence": microstructure_evidence,
        "status": "QUALIFIED" if qualified else "BLOCKED",
        "independent_oos_qualified": qualified,
        "candidate": winner if qualified else None,
        "current_candidate": winner if qualified else None,
        "descriptive_challenger": descriptive,
        "qualification_gates": gates,
        "qualification_gate_details": qualification_gate_details(
            gates,
            {**gate_evidence, **((winner or {}).get("qualification_gate_evidence") or {})},
            current_generation_available=bool(genome.get("epoch_id")),
        ),
        "qualification_gate_evidence": gate_evidence,
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "evidence": evidence,
        "blockers": blockers,
        "source_report": "safe_policy_genome_v3_report.json",
        "source_schema": genome.get("schema"),
        "live_policy_change_allowed": False,
    }
