"""Strict schemas for the Stage C results consumer.

C owns ResearchResults only. Producer CoverageReport, ReplayTrace and Stage A
records are not defined or accepted here. Unknown fields and versions fail closed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType

STUB_SCHEMA = "results_consumer_fixture_stub_v1"
STUB_VERSION = "1"
RESULTS_SCHEMA = "research_results_v1"
RESULTS_VERSION = "1"
PROFILE_SCHEMA = "gate_method_profile_v1"
PROFILE_VERSION = "1"
LEDGER_SCHEMA = "exposure_ledger_v1"
PACKAGE_SCHEMA = "strategy_draft_package_v1"
PROJECTION_SCHEMA = "dashboard_aggregate_projection_v1"

PRODUCER_SCHEMAS = frozenset(
    {
        "CoverageReport",
        "coverage_report",
        "coverage_report_v1",
        "ReplayTrace",
        "replay_trace_v1",
        "CausalRecords",
        "causal_records_v1",
        "normalized_causal_records_v1",
    }
)

_NUM = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")

STUB_KEYS = (
    "schema",
    "version",
    "fixture_only",
    "compatibility",
    "label",
    "producer_pins",
    "profile_id",
    "connection_state",
    "source",
    "as_of",
    "fixture_status",
    "scope",
    "family",
    "trials",
    "p_values",
    "coverage",
    "comparable_family",
    "rc_draws",
    "accounting",
    "folds",
    "causal",
    "exposure_ledger",
    "exposure_claim",
    "cost",
    "strategy_drafts",
    "draft_extras",
    "dashboard",
    "record_labels",
    "quarantine_findings",
)

OUTPUT_KEYS = (
    "schema",
    "version",
    "fixture_only",
    "compatibility_label",
    "evaluation_status",
    "research_done",
    "mission_done",
    "live",
    "profile",
    "producer_compatibility",
    "scope",
    "decisions",
    "coverage",
    "trials",
    "statistics",
    "methods",
    "metrics",
    "folds",
    "causal",
    "exposure",
    "cost",
    "packages",
    "dashboard",
    "labels",
    "quarantine",
    "artifacts",
    "unknowns",
    "zero_result_statement",
)

FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "eta",
        "strategy_profitable",
        "research_complete",
        "mission_complete",
        "confidence",
        "edge",
        "pass",
    }
)

EVALUATION_STATUS = "BLOCKED_NO_APPROVED_REAL_PROFILE"

UNKNOWNS = (
    "stage_a_implementation_pin_absent",
    "stage_b_implementation_pin_absent",
    "coverage_report_producer_pin_absent_schema_not_redefined",
    "approved_real_gate_method_profile_absent",
    "real_n_null",
    "real_dates_null",
    "real_cost_thresholds_null",
    "fee_verification_evidence_null",
    "measured_real_execution_friction_blocked",
    "funding_excluded_not_modelled_not_zero",
    "queue_delay_synthetic_only",
    "romano_wolf_proposed_not_tested",
    "pbo_proposed_not_tested",
    "diversity_dedup_proposed_not_tested",
    "bootstrap_b2000_seed20261104_block1day_proposed_not_tested",
    "deflated_sharpe_requirement_retained_not_tested",
    "effective_n_requirement_retained_not_tested",
    "cluster_ci_requirement_retained_not_tested",
    "nested_walk_forward_dated_definitions_not_approved",
    "white_rc_real_inference_blocked_preregistration_absent",
    "dashboard_ui_api_mapping_boss_owned_not_connected",
    "epoch_07d_audit_only",
    "no_strategy_profitability_claim",
    "no_research_done_claim",
    "realistic_v1_r7_reconciliation_rk_owned_open",
    "r53_future_live_criteria_not_this_threshold",
    "danish_arm_not_this_card",
    "live_off",
    "a2_unseen_not_inherited",
    "old_n_not_inherited",
)


class ResultsGatesError(Exception):
    code = "error"


class SchemaError(ResultsGatesError):
    code = "schema"


class ProfileRejected(ResultsGatesError):
    code = "profile"


class ProhibitedAccess(ResultsGatesError):
    code = "prohibited"


class CanaryRejected(ResultsGatesError):
    code = "canary"


class CapStop(ResultsGatesError):
    code = "incomplete_stop"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = "INCOMPLETE_STOP"


class DriftStop(ResultsGatesError):
    code = "drift"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def to_plain(obj):
    if isinstance(obj, Mapping):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [to_plain(v) for v in obj]
    return obj


def reject_floats(obj, path: str = "$") -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, float):
        raise SchemaError(f"float rejected at {path}")
    if isinstance(obj, (str, int)) or obj is None:
        return
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise SchemaError(f"non-string key at {path}")
            reject_floats(value, f"{path}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            reject_floats(value, f"{path}[{index}]")
        return
    raise SchemaError(f"unsupported type at {path}")


def canonical_bytes(obj) -> bytes:
    plain = to_plain(obj)
    reject_floats(plain)
    payload = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return payload.encode("utf-8") + b"\n"


def sha256_obj(obj) -> str:
    return sha256_bytes(canonical_bytes(obj))


NONFILL_STATES = frozenset({"missed", "rejected", "unfilled"})


def assert_nonfill_zero(state: str, net) -> None:
    """Reject a nonzero missed, rejected, or unfilled net. Do not replace it with zero."""
    if state not in NONFILL_STATES:
        return
    if net is None or parse_number(net) != 0:
        raise SchemaError("nonzero non-fill")


def parse_number(raw) -> Fraction:
    if isinstance(raw, bool) or raw is None:
        raise SchemaError("number missing")
    if isinstance(raw, float):
        raise SchemaError("float rejected")
    if isinstance(raw, Fraction):
        if raw.denominator == 0:
            raise SchemaError("nonfinite")
        return raw
    if isinstance(raw, int):
        return Fraction(raw)
    if not isinstance(raw, str) or _NUM.fullmatch(raw) is None:
        raise SchemaError("nonfinite or non-decimal number")
    try:
        value = Fraction(raw)
    except (ValueError, ZeroDivisionError) as exc:
        raise SchemaError("nonfinite or non-decimal number") from exc
    if value != value:
        raise SchemaError("nonfinite")
    return value


def number_str(value: Fraction) -> str:
    frac = Fraction(value)
    denominator = frac.denominator
    probe = denominator
    while probe % 2 == 0:
        probe //= 2
    while probe % 5 == 0:
        probe //= 5
    if probe != 1:
        return f"{frac.numerator}/{frac.denominator}"
    rendered = format(Decimal(frac.numerator) / Decimal(frac.denominator), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        return "0"
    return rendered


def exact_keys(
    doc: Mapping,
    required: tuple[str, ...],
    *,
    label: str,
    optional: tuple[str, ...] = (),
) -> None:
    if not isinstance(doc, Mapping):
        raise SchemaError(f"{label} must be an object")
    unknown = sorted(set(doc) - set(required) - set(optional))
    missing = [key for key in required if key not in doc]
    if unknown:
        raise SchemaError(f"unknown fields in {label}: {','.join(unknown)}")
    if missing:
        raise SchemaError(f"missing fields in {label}: {','.join(missing)}")


def need_str(doc: Mapping, key: str) -> str:
    value = doc[key]
    if not isinstance(value, str) or value == "":
        raise SchemaError(key)
    return value


def need_list(doc: Mapping, key: str) -> list:
    value = doc[key]
    if not isinstance(value, list):
        raise SchemaError(key)
    return value


def need_obj(doc: Mapping, key: str) -> dict:
    value = doc[key]
    if not isinstance(value, dict):
        raise SchemaError(key)
    return value


def need_int(doc: Mapping, key: str) -> int:
    value = doc[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(key)
    return value


def unique_strs(items: list, *, label: str) -> list[str]:
    if any(not isinstance(item, str) or item == "" for item in items):
        raise SchemaError(label)
    if len(items) != len(set(items)):
        raise SchemaError(f"duplicate {label}")
    return list(items)


def schema_manifest() -> dict:
    return {
        "stub_schema": STUB_SCHEMA,
        "stub_version": STUB_VERSION,
        "results_schema": RESULTS_SCHEMA,
        "results_version": RESULTS_VERSION,
        "stub_keys": list(STUB_KEYS),
        "output_keys": list(OUTPUT_KEYS),
        "producer_schemas_rejected": sorted(PRODUCER_SCHEMAS),
        "evaluation_status": EVALUATION_STATUS,
    }


def freeze(obj):
    plain = to_plain(obj)
    reject_floats(plain)
    return _freeze(plain)


def _freeze(obj):
    if isinstance(obj, dict):
        return MappingProxyType({key: _freeze(value) for key, value in obj.items()})
    if isinstance(obj, list):
        return tuple(_freeze(value) for value in obj)
    return obj


def validate_stub(doc: Mapping) -> None:
    reject_floats(doc)
    if not isinstance(doc, Mapping):
        raise SchemaError("fixture must be an object")
    schema = doc.get("schema")
    if schema in PRODUCER_SCHEMAS:
        raise SchemaError("producer schema is not owned or redefined by results_gates_v1")
    exact_keys(doc, STUB_KEYS, label="fixture")
    if doc["schema"] != STUB_SCHEMA or doc["version"] != STUB_VERSION:
        raise SchemaError("unknown fixture schema or version")
    if doc["fixture_only"] is not True:
        raise SchemaError("fixture_only")
    if doc["compatibility"] != "NOT_ESTABLISHED":
        raise SchemaError("compatibility")
    if doc["label"] != "FIXTURE-ONLY":
        raise SchemaError("label")
    if doc["connection_state"] != "NOT_CONNECTED":
        raise SchemaError("connection_state")
    if doc["fixture_status"] != "FIXTURE_ONLY":
        raise SchemaError("fixture_status")
    need_str(doc, "source")
    need_str(doc, "as_of")
    need_str(doc, "profile_id")
    pins = need_obj(doc, "producer_pins")
    exact_keys(pins, ("stage_a", "stage_b", "coverage_report"), label="producer_pins")
    for key in ("stage_a", "stage_b", "coverage_report"):
        if pins[key] != "NOT_PINNED":
            raise SchemaError("producer pin")
    scope = need_obj(doc, "scope")
    exact_keys(scope, ("exact_scope", "real_n", "real_dates", "real_cost_thresholds"), label="scope")
    need_str(scope, "exact_scope")
    for key in ("real_n", "real_dates", "real_cost_thresholds"):
        if scope[key] is not None:
            raise SchemaError(key)
    _validate_family(need_list(doc, "family"))
    _validate_trials(need_obj(doc, "trials"))
    _validate_p_values(need_list(doc, "p_values"))
    _validate_coverage(need_obj(doc, "coverage"))
    _validate_family_series(need_obj(doc, "comparable_family"))
    _validate_draws(need_list(doc, "rc_draws"))
    _validate_accounting(need_obj(doc, "accounting"))
    _validate_folds(need_obj(doc, "folds"))
    _validate_causal(need_obj(doc, "causal"))
    _validate_ledger(need_obj(doc, "exposure_ledger"))
    _validate_claim(need_obj(doc, "exposure_claim"))
    _validate_cost(need_obj(doc, "cost"))
    _validate_drafts(need_list(doc, "strategy_drafts"))
    _validate_extras(need_obj(doc, "draft_extras"))
    _validate_dashboard(need_obj(doc, "dashboard"))
    _validate_labels(need_obj(doc, "record_labels"))
    _validate_findings(need_list(doc, "quarantine_findings"))


def _validate_family(family: list) -> None:
    seen = set()
    for row in family:
        exact_keys(row, ("id", "role"), label="family")
        ident = need_str(row, "id")
        if row["role"] != "CANDIDATE":
            raise SchemaError("family role")
        if ident in seen:
            raise SchemaError("duplicate family id")
        seen.add(ident)


def _validate_trials(trials: dict) -> None:
    exact_keys(trials, ("rows", "diversity_filter_ids"), label="trials")
    rows = need_list(trials, "rows")
    seen = set()
    for row in rows:
        exact_keys(row, ("id", "kind"), label="trial")
        ident = need_str(row, "id")
        if row["kind"] not in {"strategy", "window", "tuning", "direction", "temporal", "retry"}:
            raise SchemaError("trial kind")
        if ident in seen:
            raise SchemaError("duplicate trial")
        seen.add(ident)
    diversity = trials["diversity_filter_ids"]
    if diversity is not None:
        unique_strs(diversity, label="diversity_filter_ids")


def _validate_p_values(rows: list) -> None:
    seen = set()
    for row in rows:
        exact_keys(row, ("id", "evaluated", "p"), label="p_value")
        ident = need_str(row, "id")
        if not isinstance(row["evaluated"], bool):
            raise SchemaError("evaluated")
        if ident in seen:
            raise SchemaError("duplicate p value")
        seen.add(ident)
        if row["evaluated"]:
            parse_number(row["p"])
            if parse_number(row["p"]) < 0 or parse_number(row["p"]) > 1:
                raise SchemaError("p out of range")
        elif row["p"] is not None:
            raise SchemaError("non-evaluated p must be null")


def _validate_coverage(cov: dict) -> None:
    keys = ("planned", "evaluated", "excluded", "remaining", "failed_attempts", "gate_failures")
    exact_keys(cov, keys, label="coverage")
    for key in keys:
        unique_strs(need_list(cov, key), label=key)


def _validate_family_series(block: dict) -> None:
    exact_keys(block, ("preregistered_ids", "presented_ids", "series"), label="comparable_family")
    unique_strs(need_list(block, "preregistered_ids"), label="preregistered_ids")
    unique_strs(need_list(block, "presented_ids"), label="presented_ids")
    seen = set()
    width = None
    for row in need_list(block, "series"):
        exact_keys(row, ("id", "differential"), label="series")
        ident = need_str(row, "id")
        if ident in seen:
            raise SchemaError("duplicate series")
        seen.add(ident)
        values = need_list(row, "differential")
        if not values:
            raise SchemaError("empty differential")
        for value in values:
            parse_number(value)
        if width is None:
            width = len(values)
        elif len(values) != width:
            raise SchemaError("misaligned differential")


def _validate_draws(draws: list) -> None:
    if not draws:
        return
    width = None
    for draw in draws:
        if not isinstance(draw, list) or not draw:
            raise SchemaError("rc draw")
        for index in draw:
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise SchemaError("rc draw index")
        if width is None:
            width = len(draw)
        elif len(draw) != width:
            raise SchemaError("rc draw width")


def _validate_accounting(block: dict) -> None:
    exact_keys(block, ("signals", "valuation"), label="accounting")
    if block["valuation"] != "SYNTHETIC_DECLARED":
        raise SchemaError("valuation")
    seen = set()
    orders = set()
    for row in need_list(block, "signals"):
        exact_keys(
            row,
            ("id", "state", "net", "slippage", "hold_seconds", "day", "week", "order_t"),
            label="signal",
        )
        ident = need_str(row, "id")
        if ident in seen:
            raise SchemaError("duplicate signal")
        seen.add(ident)
        if row["state"] not in {"filled", "missed", "rejected", "unfilled", "unresolved"}:
            raise SchemaError("signal state")
        order_t = need_int(row, "order_t")
        if order_t in orders:
            raise SchemaError("duplicate order_t")
        orders.add(order_t)
        if row["state"] == "unresolved":
            if row["net"] is not None:
                raise SchemaError("unresolved net")
        else:
            parse_number(row["net"])
            assert_nonfill_zero(row["state"], row["net"])
        if row["state"] == "filled":
            parse_number(row["slippage"])
            parse_number(row["hold_seconds"])
            if parse_number(row["hold_seconds"]) < 0:
                raise SchemaError("negative hold")
            need_str(row, "day")
            need_str(row, "week")
        else:
            if row["slippage"] is not None or row["hold_seconds"] is not None:
                raise SchemaError("non-fill friction")
            if row["day"] is not None:
                need_str(row, "day")
            if row["week"] is not None:
                need_str(row, "week")


def _validate_folds(block: dict) -> None:
    exact_keys(block, ("reserved_holdout_ids", "folds"), label="folds")
    unique_strs(need_list(block, "reserved_holdout_ids"), label="reserved_holdout_ids")
    folds = need_list(block, "folds")
    seen = set()
    for row in folds:
        exact_keys(
            row,
            (
                "id",
                "net",
                "train_end_t",
                "embargo_start_t",
                "embargo_end_t",
                "test_start_t",
                "test_end_t",
                "oof",
            ),
            label="fold",
        )
        ident = need_str(row, "id")
        if ident in seen:
            raise SchemaError("duplicate fold")
        seen.add(ident)
        parse_number(row["net"])
        for key in ("train_end_t", "embargo_start_t", "embargo_end_t", "test_start_t", "test_end_t"):
            need_int(row, key)
        oof_ids = []
        for trade in need_list(row, "oof"):
            exact_keys(trade, ("id", "t"), label="oof")
            oof_ids.append(need_str(trade, "id"))
            need_int(trade, "t")
        unique_strs(oof_ids, label="oof id")


def _validate_causal(block: dict) -> None:
    exact_keys(block, ("observations", "decisions"), label="causal")
    seen = set()
    for row in need_list(block, "observations"):
        exact_keys(row, ("id", "available_t", "value"), label="observation")
        ident = need_str(row, "id")
        if ident in seen:
            raise SchemaError("duplicate observation")
        seen.add(ident)
        need_int(row, "available_t")
        need_str(row, "value")
    seen_d = set()
    for row in need_list(block, "decisions"):
        exact_keys(row, ("id", "decision_t", "used_observation_ids"), label="decision")
        ident = need_str(row, "id")
        if ident in seen_d:
            raise SchemaError("duplicate decision")
        seen_d.add(ident)
        need_int(row, "decision_t")
        unique_strs(need_list(row, "used_observation_ids"), label="used_observation_ids")


def _validate_ledger(block: dict) -> None:
    exact_keys(
        block,
        ("schema", "version", "coordinate_system", "real_dates", "completeness", "intervals"),
        label="exposure_ledger",
    )
    if block["schema"] != LEDGER_SCHEMA or block["version"] != "1":
        raise SchemaError("exposure ledger schema")
    if block["coordinate_system"] != "SYNTHETIC_INDEX":
        raise SchemaError("coordinate_system")
    if block["real_dates"] is not None:
        raise SchemaError("real dates are not accepted without an approved receipt channel")
    if block["completeness"] not in {"COMPLETE", "INCOMPLETE", "UNKNOWN"}:
        raise SchemaError("ledger completeness")
    seen = set()
    for row in need_list(block, "intervals"):
        exact_keys(
            row,
            ("id", "start", "end", "disposition"),
            label="interval",
            optional=("width_mark", "provenance"),
        )
        ident = need_str(row, "id")
        if ident in seen:
            raise SchemaError("duplicate interval")
        seen.add(ident)
        mark = row.get("width_mark", "NONE")
        if mark not in {"NONE", "EMPTY"}:
            raise SchemaError("width_mark")
        provenance = row.get("provenance", None)
        if mark == "EMPTY":
            if not isinstance(provenance, str) or provenance == "":
                raise SchemaError("empty provenance")
        elif provenance is not None:
            raise SchemaError("provenance")
        if row["start"] is not None:
            need_int(row, "start")
        if row["end"] is not None:
            need_int(row, "end")
        if row["disposition"] not in {"EXPOSED", "AUDIT_ONLY", "UNKNOWN"}:
            raise SchemaError("interval disposition")


def _validate_claim(block: dict) -> None:
    exact_keys(block, ("start", "end", "token"), label="exposure_claim")
    if block["start"] is not None:
        need_int(block, "start")
    if block["end"] is not None:
        need_int(block, "end")
    need_str(block, "token")


def _validate_cost(block: dict) -> None:
    exact_keys(block, ("gross", "synthetic_roundtrip", "queue_delay_status"), label="cost")
    if block["gross"] is not None:
        parse_number(block["gross"])
    if block["synthetic_roundtrip"] is not None:
        parse_number(block["synthetic_roundtrip"])
    if block["queue_delay_status"] != "SYNTHETIC":
        raise SchemaError("queue_delay_status")


def _validate_drafts(rows: list) -> None:
    seen = set()
    for row in rows:
        exact_keys(row, ("strategy_id", "family_member_id", "definition", "extra_name"), label="draft")
        ident = need_str(row, "strategy_id")
        if ident in seen:
            raise SchemaError("duplicate draft")
        seen.add(ident)
        need_str(row, "family_member_id")
        need_str(row, "extra_name")
        definition = need_obj(row, "definition")
        exact_keys(definition, ("members", "ordered", "paper_only", "relay_eligible"), label="definition")
        unique_strs(need_list(definition, "members"), label="members")
        if not isinstance(definition["ordered"], bool):
            raise SchemaError("ordered")
        if definition["paper_only"] is not True or definition["relay_eligible"] is not False:
            raise SchemaError("draft must be paper-only and relay-ineligible")


def _validate_extras(extras: dict) -> None:
    for key, value in extras.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise SchemaError("draft extra")


def _validate_dashboard(block: dict) -> None:
    exact_keys(
        block,
        (
            "required_panels",
            "mapped_panels",
            "required_downloads",
            "mapped_downloads",
            "freshness_evidence",
        ),
        label="dashboard",
    )
    for key in ("required_panels", "mapped_panels", "required_downloads", "mapped_downloads"):
        unique_strs(need_list(block, key), label=key)
    evidence = block["freshness_evidence"]
    if evidence is not None and (not isinstance(evidence, str) or not evidence.startswith("SYNTHETIC:")):
        raise SchemaError("freshness_evidence")


def _validate_labels(block: dict) -> None:
    exact_keys(block, ("exploratory_two_month_only", "invalid_for_fills"), label="record_labels")
    unique_strs(need_list(block, "exploratory_two_month_only"), label="exploratory_two_month_only")
    unique_strs(need_list(block, "invalid_for_fills"), label="invalid_for_fills")


def _validate_findings(rows: list) -> None:
    for row in rows:
        exact_keys(row, ("epoch", "disposition"), label="quarantine_finding")
        need_str(row, "epoch")
        need_str(row, "disposition")


def validate_zero_statement(statement) -> None:
    if statement is None:
        return
    exact_keys(
        statement,
        ("text_scope", "mission_done", "research_done"),
        label="zero_result_statement",
    )
    need_str(statement, "text_scope")
    if statement["mission_done"] is not False or statement["research_done"] is not False:
        raise SchemaError("zero-result statement cannot claim mission or research DONE")


def validate_output(result: Mapping) -> None:
    reject_floats(result)
    exact_keys(result, OUTPUT_KEYS, label="ResearchResults")
    if result["schema"] != RESULTS_SCHEMA or result["version"] != RESULTS_VERSION:
        raise SchemaError("results schema")
    if result["evaluation_status"] != EVALUATION_STATUS:
        raise SchemaError("evaluation status")
    if result["research_done"] is not False or result["mission_done"] is not False:
        raise SchemaError("done flag")
    if result["live"] != "OFF":
        raise SchemaError("live")
    if result["fixture_only"] is not True:
        raise SchemaError("fixture_only")
    if result["compatibility_label"] != "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED":
        raise SchemaError("compatibility label")
    if result["scope"]["real_n"] is not None or result["scope"]["real_dates"] is not None:
        raise SchemaError("real scope")
    if result["scope"]["real_cost_thresholds"] is not None:
        raise SchemaError("real cost")
    if result["cost"]["funding_numeric"] is not None:
        raise SchemaError("funding numeric")
    if result["cost"]["fee_verification_evidence"] is not None:
        raise SchemaError("fee evidence")
    if result["cost"]["measured_real_execution_friction"] is not None:
        raise SchemaError("measured friction")
    dashboard = result["dashboard"]
    if dashboard["connection_state"] != "NOT_CONNECTED":
        raise SchemaError("dashboard connection")
    for row in list(dashboard["panels"]) + list(dashboard["downloads"]):
        for key in ("source", "as_of", "fixture_status", "connection_state"):
            if key not in row:
                raise SchemaError(f"projection missing {key}")
        if row["connection_state"] != "NOT_CONNECTED":
            raise SchemaError("projection connection")
    validate_zero_statement(result["zero_result_statement"])
    if tuple(result["unknowns"]) != UNKNOWNS:
        raise SchemaError("unknowns list")
    _reject_forbidden(result)


def _reject_forbidden(obj, path: str = "$") -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if key in FORBIDDEN_OUTPUT_KEYS:
                raise SchemaError(f"forbidden field {key}")
            _reject_forbidden(value, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _reject_forbidden(value)
