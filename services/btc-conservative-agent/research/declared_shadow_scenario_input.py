"""Bind explicit pinned cost assumptions to one actual analyzer generation.

No defaults are extracted from a model name, paper fill, or venue symbol.
Runtime baseline declarations do not supply a funding treatment or an entry
fee role, so they cannot silently become terminal economics. This pure adapter
requires the complete explicit scenario; it never qualifies a strategy.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import re

from research.declared_shadow_model import GENERATION_FIELDS, validate_contract
from research.policy_evidence_schema import stable_hash

SCHEMA = "declared_shadow_scenario_input_v1"
MAX_BYTES = 2 * 1024 * 1024
SOURCE_FIELDS = ("epoch_id", "source_revision", "deployed_revision", "tile_config_signature")


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("SCENARIO_INPUT_DUPLICATE_KEY")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("SCENARIO_INPUT_NONFINITE")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _timestamp(value, code):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(code)
    return value


def bind_declared_shadow_scenario(raw, *, expected_sha256, expected_generation,
                                  first_signal_ts):
    """Return a signed declared contract, or UNKNOWN with no fabricated costs.

    ``first_signal_ts`` must be the earliest signal in the caller's verified
    publication cohort, not a timestamp chosen from surviving profitable rows.
    The scenario's timestamp is an explicit declaration, not proof that a file
    existed historically. These checks do not authorize retrospective holdout
    selection or turn assumptions into measured exchange evidence.
    """
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
            raise ValueError("SCENARIO_INPUT_MISSING_OR_OVERSIZED")
        if (not isinstance(expected_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                or hashlib.sha256(raw).hexdigest() != expected_sha256):
            raise ValueError("SCENARIO_INPUT_HASH_MISMATCH")
        scenario = _decode(raw)
        if not isinstance(scenario, Mapping) or scenario.get("schema") != SCHEMA:
            raise ValueError("SCENARIO_INPUT_SCHEMA_INVALID")
        if (not isinstance(expected_generation, Mapping)
                or any(not isinstance(expected_generation.get(key), str)
                       or expected_generation[key].strip().upper() in {"", "UNKNOWN", "UNAVAILABLE"}
                       for key in GENERATION_FIELDS)):
            raise ValueError("SCENARIO_GENERATION_MISSING")
        for key, length in (("source_revision", 40), ("deployed_revision", 40), ("tile_config_signature", 64)):
            if not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", expected_generation[key]):
                raise ValueError("SCENARIO_SOURCE_IDENTITY_INVALID")
        identity = {key: expected_generation[key] for key in SOURCE_FIELDS}
        if scenario.get("source_identity") != identity:
            raise ValueError("SCENARIO_SOURCE_COHORT_MISMATCH")
        first = _timestamp(first_signal_ts, "SCENARIO_COHORT_SIGNAL_TIME_MISSING")
        declared = _timestamp(scenario.get("declared_at_ts"), "SCENARIO_DECLARATION_TIME_MISSING")
        if declared > first:
            raise ValueError("SCENARIO_POST_SIGNAL_DECLARATION")
        if scenario.get("evidence_basis") != "DECLARED_SIMULATION":
            raise ValueError("SCENARIO_EVIDENCE_BASIS_INVALID")
        for key in ("model_id", "provenance"):
            if not isinstance(scenario.get(key), str) or not scenario[key].strip():
                raise ValueError("SCENARIO_PROVENANCE_MISSING")
        for key in ("fee_rates", "funding", "latency"):
            if not isinstance(scenario.get(key), Mapping):
                raise ValueError("SCENARIO_" + key.upper() + "_MISSING")
        body = {"schema": "declared_shadow_model_v1", "evidence_basis": "DECLARED_SIMULATION",
                "generation": deepcopy(dict(expected_generation)),
                "model_id": scenario["model_id"], "provenance": scenario["provenance"],
                "source_config_sha256": expected_sha256,
                "source_config_basis": "PINNED_DECLARED_SCENARIO_NOT_EXCHANGE_OBSERVATION",
                "declared_at_ts": declared, "first_cohort_signal_ts": first,
                "source_identity": identity,
                "fee_rates": deepcopy(dict(scenario["fee_rates"])),
                "funding": deepcopy(dict(scenario["funding"])),
                "latency": deepcopy(dict(scenario["latency"])),
                "qualification_eligible": False, "live_arming_authorized": False}
        contract = {**body, "signature": stable_hash("declared-shadow-model", body)}
        validate_contract(contract, expected_generation)
        return {"status": "DECLARED_MODEL_READY", "contract": contract, "reason_codes": [],
                "scenario_input_sha256": expected_sha256,
                "evidence_basis": "DECLARED_SIMULATION", "qualification_eligible": False,
                "live_arming_authorized": False}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        # JSON/parser errors can include untrusted text; publish only static codes.
        reason = str(exc)
        if not re.fullmatch(r"(?:SCENARIO|DECLARED_MODEL)_[A-Z_]+", reason):
            reason = "SCENARIO_INPUT_INVALID"
        return {"status": "UNKNOWN", "contract": None, "reason_codes": [reason],
                "evidence_basis": "DECLARED_SIMULATION", "qualification_eligible": False,
                "live_arming_authorized": False}
