"""Research-only operation/input boundary; no tape/golden/case-ID access.

All domain decisions occur in the real implementation functions in OPERATION_MAP.
Candidate returns an object as required; HM currently tapes it as a scalar string
(a disclosed contract HOLD, not silently unwrapped). No engine/restart is started.
"""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79"
P8_SPEC_SHA256 = "28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from . import pin7_classifier as c
from . import pin_scoring as s
from . import pin_k3 as k3
from . import pin_research_contracts as contracts
from . import pin_fill_operations as fills

# Synthetic constants from Pin7/P8 example fixtures; not live calibration defaults.
SYNTHETIC_THRESHOLDS = dict(v90=.02, v95=.03, e50=.30, e75=.60)

OPERATION_MAP = {
    "features": c.candle_features,
    "versioned_features": c.versioned_features,
    "calibration": c.calibrate,
    "candidate": c.candidate,
    "fsm": c.replay,
    "cap": c.cap_allows,
    "observe": c.observe,
    "safety": c.safety_admission,
    "utc_days": s.utc_days,
    "coverage": s.pin8_coverage,
    "branch_ledger": s.branch_ledger,
    "pin15": s.score_pin15,
    "pin15_counts": s.pin15_counts,
    "dual": s.dual_from_pvalue,
    "grammar_select": contracts.select_ids,
    "frozen_ids": contracts.frozen_ids,
    "prior_c0": contracts.prior_calibration,
    "role_mask": contracts.role_mask,
    "runtime_gate": contracts.runtime_gate,
    "eligibility": contracts.selection_eligibility,
    "ct_lookup": contracts.ct_lookup,
    "b3_guard": contracts.b3_guard,
    "k3": k3.score_k3,
    "falsekill": k3.falsekill_probability,
    "executable_l1_mtm": k3.executable_l1_mtm,
    "field9_fill_model": fills.evaluate_fill,
    "field9_historical_control": fills.historical_control,
}


def _invoke(operation, data, run_root=None):
    function = OPERATION_MAP[operation]
    if operation == "features":
        return function(data["bars"], data["decision"], availability_mode=data.get("availability_mode", "forward"), symbol=data.get("symbol", c.SYMBOL))
    if operation == "calibration":
        return function(data["pairs"], fixed_interval=data.get("fixed_interval", True), allowlisted=data.get("allowlisted", True))
    if operation == "candidate":
        return dict(candidate=function(data["V"], data["E"], data["z"], data["current"], SYNTHETIC_THRESHOLDS))
    if operation == "cap":
        return dict(allowed=function(data["flips"], data["h"]))
    if operation in ("fsm", "observe"):
        return function(data, SYNTHETIC_THRESHOLDS, run_root=run_root)
    if operation in ("b3_guard", "k3"):
        return function(data, run_root=run_root)
    if operation == "utc_days":
        return function(data["seconds"])
    if operation == "branch_ledger":
        return function(data["closed"])
    if operation == "pin15_counts":
        return function(data["rows"])
    if operation == "pin15":
        # These vectors condition on a filled basis, not on executable L1 pricing.
        data.pop("l1", None)
        return function(**data)
    if operation in ("coverage", "ct_lookup"):
        return function(**data)
    if operation == "grammar_select":
        return function(data.get("grammar", []), input_context=data)
    if operation == "falsekill":
        return function()
    return function(data)


def evaluate(operation, input):
    """Return a strict finite JSON object. The two arguments contain no case ID.

Persistence is synthetic unit serialization only, under an existing absolute
RUNROOT selected by the caller's reviewed execution context, never input paths.
Invalid/nonfinite outputs raise rather than being sanitized or concealed.
"""
    if operation not in OPERATION_MAP:
        raise ValueError("UNSUPPORTED_OPERATION:" + str(operation))
    if not isinstance(input, dict):
        raise TypeError("INPUT_OBJECT_REQUIRED")
    json.dumps(input, allow_nan=False)
    data = copy.deepcopy(input)
    persistence = operation in ("fsm", "observe", "b3_guard", "k3") and bool(data.get("restart_at") or data.get("restart_after"))
    if persistence:
        raw = os.environ.get("RUNROOT")
        if not raw or not Path(raw).is_absolute() or not Path(raw).is_dir():
            raise ValueError("EXISTING_ABSOLUTE_RUNROOT_REQUIRED")
        run_root = Path(raw).resolve(strict=True)
        with TemporaryDirectory(prefix="p8-synthetic-", dir=run_root) as temp:
            output = _invoke(operation, data, run_root=temp)
    else:
        output = _invoke(operation, data)
    if not isinstance(output, dict):
        raise TypeError("OUTPUT_OBJECT_REQUIRED")
    # No replacement of NaN/Infinity: fail closed with no decision output.
    json.dumps(output, allow_nan=False)
    return output
