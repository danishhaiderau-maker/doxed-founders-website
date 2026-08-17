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
    # Cont-57bb cohort tags: classify divergence without discarding authenticated
    # Bitfinex Scenario C PnL merely because MIRROR_DIFF fired.
    if "SHOWCASE_ONLY_RELAY_PAUSED" in negative_event_names or row.get("showcase_only_relay_paused") is True:
        fidelity.append("SHOWCASE_ONLY_RELAY_PAUSED")
    copy_fill = row.get("copy_fill_observed") if isinstance(row.get("copy_fill_observed"), dict) else {}
    if not copy_fill:
        copy_fill = evidence.get("copy_fill_observed") if isinstance(evidence.get("copy_fill_observed"), dict) else {}
    if (
        str(copy_fill.get("classification") or "").upper() == "COPY_ONLY_FILL_AUTHENTICATED_SOURCE_UNCONFIRMED"
        or str(copy_fill.get("source_model_fill_state") or "").upper() == "SOURCE_UNCONFIRMED"
        or row.get("copy_only_source_unconfirmed") is True
    ):
        # Source paper never confirmed; keep Bitfinex fidelity/PnL cohortable.
        showcase.append("COPY_ONLY_SOURCE_UNCONFIRMED")
    market_evidence = row.get("source_order_market_evidence") if isinstance(row.get("source_order_market_evidence"), dict) else {}
    latest_obs = market_evidence.get("latest_observation") if isinstance(market_evidence.get("latest_observation"), dict) else {}
    original_limit = market_evidence.get("original_limit_price")
    current_limit = market_evidence.get("current_limit_price", market_evidence.get("limit_price"))
    obs_limit = latest_obs.get("limit_price")
    if (
        original_limit is not None
        and obs_limit is not None
        and current_limit is not None
        and abs(float(obs_limit) - float(current_limit)) >= 0.005
        and abs(float(obs_limit) - float(original_limit)) < 0.005
        and int(market_evidence.get("limit_generation") or latest_obs.get("limit_generation") or 0) > 0
    ):
        fidelity.append("STALE_LIMIT_EVIDENCE")
    if mirror_stale_lifecycle:
        fidelity.append("MIRROR_DIFF_STALE_NO_EXPOSURE")
    elif not provenance:
        fidelity.append("TERMINAL_PROVENANCE_MISSING")
    elif provenance in _EXCLUDED_PROVENANCE:
        append_provenance_exclusion(fidelity)

    optimisation = reasons[REAL_COPY_PARAMETER_OPTIMISATION]
    optimisation.extend(showcase)
    optimisation.extend(fidelity)
    # SHOWCASE_ONLY_RELAY_PAUSED has no Bitfinex participant — keep out of real-copy opt.
    if "SHOWCASE_ONLY_RELAY_PAUSED" in fidelity:
        optimisation.append("SHOWCASE_ONLY_RELAY_PAUSED")
    # Authenticated BF Scenario C PnL remains optimisable even with MIRROR_DIFF /
    # COPY_ONLY_SOURCE_UNCONFIRMED as long as actual PnL + costs are present.
    while "COPY_ONLY_SOURCE_UNCONFIRMED" in optimisation:
        optimisation.remove("COPY_ONLY_SOURCE_UNCONFIRMED")
    while "MIRROR_DIFF" in optimisation:
        optimisation.remove("MIRROR_DIFF")
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
