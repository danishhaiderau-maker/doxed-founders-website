"""Five-cohort eligibility contract for BTC research rows.

Showcase strategy, Bitfinex copy-fidelity, correlated-cluster-blocked
counterfactuals, copy-only exchange fills, and real-copy optimisation are
non-overlapping research questions. Missing evidence excludes a row from the
affected cohort. A Bitfinex fill never rewrites Showcase fill truth.
"""
from collections import Counter

SHOWCASE_STRATEGY = "SHOWCASE_STRATEGY"
BITFINEX_COPY_FIDELITY = "BITFINEX_COPY_FIDELITY"
CORRELATED_CLUSTER_BLOCKED = "CORRELATED_CLUSTER_BLOCKED"
COPY_ONLY_EXCHANGE_FILLS = "COPY_ONLY_EXCHANGE_FILLS"
REAL_COPY_PARAMETER_OPTIMISATION = "REAL_COPY_PARAMETER_OPTIMISATION"

COHORTS = (
    SHOWCASE_STRATEGY,
    BITFINEX_COPY_FIDELITY,
    CORRELATED_CLUSTER_BLOCKED,
    COPY_ONLY_EXCHANGE_FILLS,
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
    "SHOWCASE_ABANDONED_LATE_FILL_CLEANUP",
    "STALE_NO_EXPOSURE",
    "MIRROR_DIFF_STALE_NO_EXPOSURE",
    "CORRELATED_CLUSTER_BLOCKED",
}

_COPY_ONLY_TERMINALS = {
    "LATE_FILL_CLEANUP",
    "SHOWCASE_ABANDONED_LATE_FILL_CLEANUP",
    "SHOWCASE_MIRROR",
}


def _present(value):
    return value is not None and value != "" and value != [] and value != {}


def _evidence(row):
    evidence = row.get("bitfinex_evidence")
    return evidence if isinstance(evidence, dict) else {}


def _copy_fill(row, evidence):
    copy_fill = row.get("copy_fill_observed") if isinstance(row.get("copy_fill_observed"), dict) else {}
    if not copy_fill:
        copy_fill = evidence.get("copy_fill_observed") if isinstance(evidence.get("copy_fill_observed"), dict) else {}
    return copy_fill


def _is_copy_only(row, evidence, copy_fill):
    classification = str(copy_fill.get("classification") or "").upper()
    source_model = str(copy_fill.get("source_model_fill_state") or "").upper()
    divergence = str(
        row.get("divergence_class")
        or evidence.get("divergence_class")
        or copy_fill.get("divergence_reason")
        or ""
    ).upper()
    source_status = str(row.get("source_fill_status") or evidence.get("source_fill_status") or "").upper()
    copy_status = str(row.get("copy_fill_status") or evidence.get("copy_fill_status") or "").upper()
    return bool(
        row.get("copy_only_source_unconfirmed") is True
        or classification in {
            "COPY_ONLY_FILL_AUTHENTICATED_SOURCE_UNCONFIRMED",
            "COPY_FILLED_SOURCE_PENDING_OR_UNKNOWN",
        }
        or source_model in {"SOURCE_UNCONFIRMED", "INDEPENDENT_OR_PENDING"}
        or divergence in {"COPY_ONLY_PARTIAL_FILL", "COPY_FILLED_SOURCE_UNFILLED_OR_UNKNOWN"}
        or (
            source_status in {"UNFILLED", "UNKNOWN"}
            and copy_status in {"PARTIAL", "FILLED"}
        )
    )


def _cluster_blocked(row, evidence, lifecycle_events, negative_event_names, provenance):
    return bool(
        provenance == "CORRELATED_CLUSTER_BLOCKED"
        or "CORRELATED_CLUSTER_BLOCKED" in lifecycle_events
        or "CORRELATED_CLUSTER_BLOCKED" in negative_event_names
        or "CORRELATED_CLUSTER_BLOCKED" in set(evidence.get("analysis_exclusion_reasons") or [])
        or row.get("correlated_cluster_blocked") is True
    )


def classify_row(row):
    """Return cohort membership and counted, cohort-specific exclusions."""
    row = row if isinstance(row, dict) else {}
    reasons = {cohort: [] for cohort in COHORTS}
    provenance = str(row.get("terminal_provenance") or row.get("terminal_class") or "").upper()
    policy = row.get("policy_snapshot") if isinstance(row.get("policy_snapshot"), dict) else {}
    policy_version = row.get("policy_version") or policy.get("policy_version")
    policy_key = row.get("policy_comparability_key") or policy.get("policy_comparability_key")
    mixed_policy = (
        row.get("mixed_policy") is True
        or "MIXED_POLICY" in set(row.get("analysis_exclusion_reasons") or [])
    )
    lifecycle_events = {
        str(event.get("event_type") or event.get("event") or event.get("eventType") or "").upper()
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
        elif provenance in {
            "EXIT_ONLY_PENDING_CANCEL_PARTIAL_FILL",
            "LATE_FILL_CLEANUP",
            "SHOWCASE_ABANDONED_LATE_FILL_CLEANUP",
        }:
            target.append("LATE_FILL_CLEANUP")
        elif provenance == "CORRELATED_CLUSTER_BLOCKED":
            target.append("CORRELATED_CLUSTER_BLOCKED")
        elif provenance in {"EMERGENCY_ACTION", "EMERGENCY_CLOSE"}:
            target.append("EMERGENCY_CLOSE")
        else:
            target.append("MANUAL_CLOSE")

    evidence = _evidence(row)
    copy_fill = _copy_fill(row, evidence)
    copy_only = _is_copy_only(row, evidence, copy_fill)
    fence_complete = bool(
        evidence.get("copy_terminal_fence_complete") is True
        or row.get("copy_terminal_fence_complete") is True
    )
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
    cluster_blocked = _cluster_blocked(
        row, evidence, lifecycle_events, negative_event_names, provenance
    )

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
    if copy_only:
        showcase.append("COPY_ONLY_SOURCE_UNCONFIRMED")
    if cluster_blocked:
        showcase.append("CORRELATED_CLUSTER_BLOCKED")
    if mirror_stale_lifecycle:
        showcase.append("MIRROR_DIFF_STALE_NO_EXPOSURE")
    elif not provenance:
        showcase.append("TERMINAL_PROVENANCE_MISSING")
    elif provenance in _EXCLUDED_PROVENANCE:
        append_provenance_exclusion(showcase)

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
    source_snapshot_required = not (copy_only and fence_complete)
    if source_snapshot_required and evidence.get("source_snapshot_evidence_complete") is not True:
        fidelity.append("SOURCE_SNAPSHOT_EVIDENCE_INCOMPLETE")
    if evidence.get("reconciliation_complete") is not True:
        fidelity.append("RECONCILIATION_INCOMPLETE")
    if "COPY_ORDER_NO_SHOWCASE" in negative_event_names:
        fidelity.append("COPY_ORDER_NO_SHOWCASE")
    if "SHOWCASE_ONLY_RELAY_PAUSED" in negative_event_names or row.get("showcase_only_relay_paused") is True:
        fidelity.append("SHOWCASE_ONLY_RELAY_PAUSED")
    if cluster_blocked:
        fidelity.append("CORRELATED_CLUSTER_BLOCKED")
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
    elif provenance in _EXCLUDED_PROVENANCE and provenance not in _COPY_ONLY_TERMINALS:
        append_provenance_exclusion(fidelity)

    copy_only_reasons = reasons[COPY_ONLY_EXCHANGE_FILLS]
    if not copy_only:
        copy_only_reasons.append("NOT_COPY_ONLY_EXCHANGE_FILL")
    if cluster_blocked:
        copy_only_reasons.append("CORRELATED_CLUSTER_BLOCKED")
    copy_only_reasons.extend(
        reason for reason in fidelity
        if reason not in {
            "SOURCE_SNAPSHOT_EVIDENCE_INCOMPLETE",
            "CORRELATED_CLUSTER_BLOCKED",
            "SHOWCASE_ONLY_RELAY_PAUSED",
        }
    )

    cluster_reasons = reasons[CORRELATED_CLUSTER_BLOCKED]
    if not cluster_blocked:
        cluster_reasons.append("NOT_CORRELATED_CLUSTER_BLOCKED")
    if not _present(row.get("trade_id")):
        cluster_reasons.append("CANONICAL_IDENTITY_MISSING")

    optimisation = reasons[REAL_COPY_PARAMETER_OPTIMISATION]
    optimisation.extend(showcase)
    optimisation.extend(fidelity)
    if "SHOWCASE_ONLY_RELAY_PAUSED" in fidelity:
        optimisation.append("SHOWCASE_ONLY_RELAY_PAUSED")
    while "COPY_ONLY_SOURCE_UNCONFIRMED" in optimisation:
        optimisation.remove("COPY_ONLY_SOURCE_UNCONFIRMED")
    while "MIRROR_DIFF" in optimisation:
        optimisation.remove("MIRROR_DIFF")
    if copy_only:
        optimisation.append("COPY_ONLY_EXCHANGE_FILL")
    if cluster_blocked:
        optimisation.append("CORRELATED_CLUSTER_BLOCKED")
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
