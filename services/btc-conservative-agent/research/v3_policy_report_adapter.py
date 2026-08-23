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


def _jsonl(path: Path) -> list[dict]:
    try:
        payload = path.read_bytes()
    except OSError:
        return []
    rows: list[dict] = []
    for raw in payload.splitlines():
        try:
            value = json.loads(raw.decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def has_v3_evidence(data_dir=".") -> bool:
    return (Path(data_dir) / "v3" / "ledgers" / "opportunity.jsonl").is_file()


def load_v3_cycle_snapshot(data_dir=".") -> dict:
    root = Path(data_dir)
    ledger_root = root / "v3" / "ledgers"
    names = ("opportunity", "decision", "order_intent", "execution", "lifecycle", "market_segment")
    rows = {name: _jsonl(ledger_root / f"{name}.jsonl") for name in names}
    opportunities = rows["opportunity"]
    digest = hashlib.sha256()
    for name in names:
        for row in rows[name]:
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
    latest = max(opportunities, key=lambda row: float(row.get("signal_ts") or 0), default={})
    epoch_id = str(latest.get("epoch_id") or "") or None
    scoped_decisions = [row for row in rows["decision"] if not epoch_id or row.get("epoch_id") == epoch_id]
    signatures = sorted({str(row.get("policy_signature")) for row in scoped_decisions if row.get("policy_signature")})
    policy_epochs = sorted({str(row.get("policy_epoch_id")) for row in scoped_decisions if row.get("policy_epoch_id")})
    return {
        "schema": "policy_cycle_snapshot_v3_1",
        "snapshot_id": "policy-v3-snapshot-" + digest.hexdigest()[:24],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_file": "v3/ledgers/*.jsonl",
        "source_read_mode": "BYTES_THEN_PARSE_V1",
        "collector_version": "collector_v3.1",
        "row_count": len(opportunities),
        "ledger_counts": {name: len(value) for name, value in rows.items()},
        "last_event_id": latest.get("episode_id"),
        "last_signal_ts": latest.get("signal_ts"),
        "epoch_id": epoch_id,
        "policy_epoch_id": policy_epochs[0] if len(policy_epochs) == 1 else None,
        "policy_epoch_ids": policy_epochs,
        "policy_signature": signatures[0] if len(signatures) == 1 else None,
        "policy_signatures": signatures,
    }


def load_or_build_genome(data_dir=".", report_dir=".") -> dict:
    from research.research_v3_report import build_safe_policy_genome_v3_report

    return build_safe_policy_genome_v3_report(data_dir=data_dir, report_dir=report_dir)


def candidate_from_genome(genome: dict, cycle_snapshot: dict, microstructure_evidence=None) -> dict:
    collection = genome.get("collection") or {}
    ranking = genome.get("safe_policy_ranking") or {}
    screen = genome.get("candidate_screen") or {}
    winner = genome.get("number_one_strategy")
    qualified = bool(winner and genome.get("qualification") not in (None, "NO_SAFE_QUALIFIED_POLICY"))
    required = (
        "chronological_untouched_oos", "cost_adjusted_positive_expectancy",
        "acceptable_drawdown", "minimum_independent_episodes",
        "parameter_neighborhood_stability", "conservative_execution",
        "regime_diversity", "no_data_integrity_defects",
        "control_benchmark_comparison",
    )
    winner_gates = (winner or {}).get("gates") or {}
    episode_count = int(collection.get("independent_opportunities") or 0)
    execution_count = int(collection.get("execution_rows") or 0)
    segment_count = int(collection.get("market_segments") or 0)
    integrity_passed = bool((genome.get("integrity") or {}).get("passed"))
    gates = {name: bool(winner_gates.get(name)) for name in required}
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
        "qualification_gate_schema": "best_policy_qualification_gates_v1",
        "evidence": evidence,
        "blockers": sorted(set(blockers)),
        "source_report": "safe_policy_genome_v3_report.json",
        "source_schema": genome.get("schema"),
        "live_policy_change_allowed": False,
    }
