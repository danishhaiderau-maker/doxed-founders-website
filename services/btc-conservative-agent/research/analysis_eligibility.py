"""Pure three-cohort eligibility contract for BTC research rows.

The contract is intentionally conservative. Missing evidence excludes a row
from the affected cohort, while Showcase-only research never requires a
Bitfinex link merely because the live-copy product exists.
"""
from collections import Counter

SHOWCASE_STRATEGY = "SHOWCASE_STRATEGY"
BITFINEX_COPY_FIDELITY = "BITFINEX_COPY_FIDELITY"
REAL_COPY_PARAMETER_OPTIMISATION = "REAL_COPY_PARAMETER_OPTIMISATION"

COHORTS = (
    SHOWCASE_STRATEGY,
    BITFINEX_COPY_FIDELITY,
    REAL_COPY_PARAMETER_OPTIMISATION,
)

_EXCLUDED_PROVENANCE = {
    "SOURCE_ABSENCE_FALLBACK",
    "SHOWCASE_POSITION_ABSENT",
    "SHOWCASE_VANISHED",
    "ADMIN_MANUAL_CLOSE",
    "MANUAL_CLOSE",
    "EMERGENCY_ACTION",
    "EMERGENCY_CLOSE",
    "SHOWCASE_UNREACHABLE_OPEN_LOT",
    "SHOWCASE_FLAT_FAILSAFE",
    "SHOWCASE_BOOK_FLAT",
    "EXIT_ONLY_PENDING_CANCEL_PARTIAL_FILL",
    "LATE_FILL_CLEANUP",
    "STALE_NO_EXPOSURE",
    "MIRROR_DIFF_STALE_NO_EXPOSURE",
}


def _present(value):
    return value is not None and value != "" and value != [] and value != {}


def _evidence(row):
    evidence = row.get("bitfinex_evidence")
    return evidence if isinstance(evidence, dict) else {}


def classify_row(row):
    """Return cohort membership and counted, cohort-specific exclusions."""
    row = row if isinstance(row, dict) else {}
    reasons = {cohort: [] for cohort in COHORTS}
    provenance = str(row.get("terminal_provenance") or "").upper()
    policy = row.get("policy_snapshot") if isinstance(row.get("policy_snapshot"), dict) else {}
    policy_version = row.get("policy_version") or policy.get("policy_version")
    policy_key = row.get("policy_comparability_key") or policy.get("policy_comparability_key")
    mixed_policy = (
        row.get("mixed_policy") is True
        or "MIXED_POLICY" in set(row.get("analysis_exclusion_reasons") or [])
    )
    lifecycle_events = {
        str(event.get("event_type") or event.get("event") or "").upper()
        for event in (row.get("lifecycle_events") or [])
        if isinstance(event, dict)
    }
    mirror_stale_lifecycle = bool(
        row.get("mirror_diff_stale_no_exposure") is True
        or (
            "MIRROR_DIFF" in lifecycle_events
            and (
                provenance == "STALE_NO_EXPOSURE"
                or "STALE_NO_EXPOSURE" in lifecycle_events
            )
        )
    )

    def append_provenance_exclusion(target):
        if mirror_stale_lifecycle:
            target.append("MIRROR_DIFF_STALE_NO_EXPOSURE")
        elif provenance in {"STALE_NO_EXPOSURE", "MIRROR_DIFF_STALE_NO_EXPOSURE"}:
            target.append("STALE_NO_EXPOSURE")
        elif provenance in {"SOURCE_ABSENCE_FALLBACK", "SHOWCASE_POSITION_ABSENT", "SHOWCASE_VANISHED"}:
            target.append("SOURCE_ABSENCE_FALLBACK")
        elif provenance in {"SHOWCASE_FLAT_FAILSAFE", "SHOWCASE_BOOK_FLAT"}:
            target.append("UNSUPPORTED_FLAT_BOOK_EXIT")
        elif provenance in {"EXIT_ONLY_PENDING_CANCEL_PARTIAL_FILL", "LATE_FILL_CLEANUP"}:
            target.append("LATE_FILL_CLEANUP")
        elif provenance in {"EMERGENCY_ACTION", "EMERGENCY_CLOSE"}:
            target.append("EMERGENCY_CLOSE")
        else:
            target.append("MANUAL_CLOSE")

    showcase = reasons[SHOWCASE_STRATEGY]
    if not _present(row.get("trade_id")):
        showcase.append("CANONICAL_IDENTITY_MISSING")
    if row.get("policy_snapshot_complete") is not True:
        showcase.append("POLICY_SNAPSHOT_INCOMPLETE")
    if not _present(policy_version):
        showcase.append("POLICY_VERSION_MISSING")
    if mixed_policy:
        showcase.append("MIXED_POLICY")
    if row.get("replay_complete") is not True:
        showcase.append("REPLAY_INCOMPLETE")
    if mirror_stale_lifecycle:
        showcase.append("MIRROR_DIFF_STALE_NO_EXPOSURE")
    elif not provenance:
        showcase.append("TERMINAL_PROVENANCE_MISSING")
    elif provenance in _EXCLUDED_PROVENANCE:
        append_provenance_exclusion(showcase)

    evidence = _evidence(row)
    negative_event_names = {
        str(
            event.get("event")
            or (event.get("payload") or {}).get("diff_type")
            or event.get("event_type")
            or ""
        ).upper()
        for event in (evidence.get("negative_events") or [])
        if isinstance(event, dict)
    }
    fidelity = reasons[BITFINEX_COPY_FIDELITY]
    if not _present(row.get("trade_id")):
        fidelity.append("CANONICAL_IDENTITY_MISSING")
    if evidence.get("linkage_complete") is not True:
        fidelity.append("BITFINEX_LINKAGE_MISSING")
    if evidence.get("quantity_evidence_complete") is not True:
        fidelity.append("QUANTITY_EVIDENCE_INCOMPLETE")
    if evidence.get("order_ack_history_complete") is not True:
        fidelity.append("ORDER_ACK_HISTORY_INCOMPLETE")
    if evidence.get("stop_evidence_complete") is not True:
        fidelity.append("STOP_EVIDENCE_INCOMPLETE")
    if evidence.get("source_snapshot_evidence_complete") is not True:
        fidelity.append("SOURCE_SNAPSHOT_EVIDENCE_INCOMPLETE")
    if evidence.get("reconciliation_complete") is not True:
        fidelity.append("RECONCILIATION_INCOMPLETE")
    if "COPY_ORDER_NO_SHOWCASE" in negative_event_names:
        fidelity.append("COPY_ORDER_NO_SHOWCASE")
    if mirror_stale_lifecycle:
        fidelity.append("MIRROR_DIFF_STALE_NO_EXPOSURE")
    elif not provenance:
        fidelity.append("TERMINAL_PROVENANCE_MISSING")
    elif provenance in _EXCLUDED_PROVENANCE:
        append_provenance_exclusion(fidelity)

    optimisation = reasons[REAL_COPY_PARAMETER_OPTIMISATION]
    optimisation.extend(showcase)
    optimisation.extend(fidelity)
    if not _present(policy_key):
        optimisation.append("POLICY_COMPARABILITY_KEY_MISSING")
    if row.get("actual_bitfinex_realized_pnl_usd") is None:
        optimisation.append("BITFINEX_ACTUAL_PNL_MISSING")
    if evidence.get("cost_evidence_complete") is not True:
        optimisation.append("EXECUTION_COST_EVIDENCE_MISSING")
    if row.get("required_post_exit_horizons_complete") is not True:
        optimisation.append("REQUIRED_POST_EXIT_HORIZON_INCOMPLETE")
    if row.get("required_entry_horizons_complete") is not True:
        optimisation.append("REQUIRED_ENTRY_HORIZON_INCOMPLETE")

    normalized = {cohort: sorted(set(values)) for cohort, values in reasons.items()}
    return {
        "schema": "analysis_cohorts_v1",
        "eligible": {cohort: not normalized[cohort] for cohort in COHORTS},
        "exclusion_reasons": normalized,
    }


def eligible_trade_ids(rows, cohort):
    if cohort not in COHORTS:
        raise ValueError(f"Unknown analysis cohort: {cohort}")
    eligible = set()
    exclusions = Counter()
    for trade_id, row in (rows or {}).items():
        materialized = dict(row) if isinstance(row, dict) else {}
        materialized.setdefault("trade_id", trade_id)
        result = classify_row(materialized)
        if result["eligible"][cohort]:
            eligible.add(str(trade_id))
        else:
            exclusions.update(result["exclusion_reasons"][cohort])
    return eligible, dict(sorted(exclusions.items()))
