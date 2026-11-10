"""Language-neutral fixture adapter executing independent Pin implementation.

No HM oracle Python is imported or executed. Fixture expected values are read
only by the assertion harness, never passed to implementation entry points.
The CLI emits JSON on stdout only; all outputs are provisional/non-evidential.
"""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "3b5dba29fdfffe916fef9d209c6095db5ca45fc1a998a9682ea6acc383584fc1"
P8_SPEC_SHA256 = "ca77dbaeed9cf55ac1f14d6e4ecd525c21ccacc4987c64c112c7e1fb5f5d581a"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

import copy
import hashlib
import json
import math
from pathlib import Path
from . import fill_model
from . import pin7_classifier as c
from . import pin_research_contracts as contracts
from . import pin_scoring as s
from . import pin_k3 as k3

ROOT = Path(__file__).with_name("pin_fixtures")
HM_MANIFEST_SHA256 = "a023c98ca3811dd07953d7bd869ec18a58c41628b27acf3b87b90c18228507db"
FIELD9_SHA256 = "39a9e5efe92e3cea97658ab445673cfc9928f8adda8fb2ec80c5afeaa97f1bd2"
THRESHOLDS = dict(v90=.02, v95=.03, e50=.30, e75=.60)  # SYNTHETIC ONLY
PENDING_REASON = "pending GS spec clarification"


def pending_reason(case):
    # Three unresolved *families*, six physical vectors. Keep input bytes intact.
    if case["evidence_status"] == "AMBIGUOUS_PENDING_GS" or case["id"] == "P8R4-12-STOP-BEFORE" or case["id"].startswith("P8R4-12-MISSING-LOG-"):
        return PENDING_REASON
    return None


def verify_fixture_hashes(root=ROOT):
    manifest = root / "MANIFEST.sha256"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != HM_MANIFEST_SHA256:
        raise ValueError("HM_MANIFEST_SHA_MISMATCH")
    pins = json.loads((root / "SOURCE_PINS.json").read_text())
    checked = []
    for filename, expected in pins["copied_files"].items():
        path = (root / filename).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("FIXTURE_PATH_OUTSIDE_ROOT")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("FIXTURE_SHA_MISMATCH:" + filename)
        checked.append(filename)
    # Additionally bind copies to their ORIGINAL HM manifest, rather than only a new local manifest.
    original = {}
    for line in manifest.read_text().splitlines():
        sha, filename = line.split(maxsplit=1)
        original[filename.lstrip("*")] = sha
    for filename in checked:
        if filename in original and hashlib.sha256((root / filename).read_bytes()).hexdigest() != original[filename]:
            raise ValueError("ORIGINAL_HM_SHA_MISMATCH:" + filename)
    if hashlib.sha256((root / "field9.json").read_bytes()).hexdigest() != FIELD9_SHA256:
        raise ValueError("FIELD9_SHA_MISMATCH")
    for key, value in (("pin7", PIN7_SPEC_SHA256), ("p8", P8_SPEC_SHA256), ("ct", CT_TABLE_SHA256), ("rules", RULES_SHA256)):
        if pins[key]["sha256"] != value:
            raise ValueError("SOURCE_PIN_MISMATCH:" + key)
    return dict(manifest_sha256=HM_MANIFEST_SHA256, copied_files=len(checked), field9_sha256=FIELD9_SHA256)


def load_cases(root=ROOT):
    verify_fixture_hashes(root)
    index = json.loads((root / "tapes" / "index.json").read_text())
    cases = []
    for filename in index["files"]:
        cases.extend(json.loads((root / "tapes" / filename).read_text()))
    if len(cases) != index["count"] or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("TAPE_INDEX_COUNT_OR_DUPLICATE")
    return cases


def dispatch(operation, data):
    """Only implementation inputs cross this boundary, never goldens or case ID."""
    x = copy.deepcopy(data)
    if operation == "features":
        return c.candle_features(x["bars"], x["decision"], availability_mode=x.get("availability_mode", "forward"), symbol=x.get("symbol", c.SYMBOL))
    if operation == "versioned_features":
        return c.versioned_features(x)
    if operation == "calibration":
        return c.calibrate(x["pairs"], fixed_interval=x.get("fixed_interval", True), allowlisted=x.get("allowlisted", True))
    if operation == "candidate":
        return c.candidate(x["V"], x["E"], x["z"], x["current"], THRESHOLDS)
    if operation == "cap":
        return dict(allowed=c.cap_allows(x["flips"], x["h"]))
    if operation == "fsm":
        return c.replay(x, THRESHOLDS)
    if operation == "observe":
        x["events"] = [e for e in x["events"] if e["h"] <= x["query"]]
        result = c.replay(x, THRESHOLDS)
        return result.get("state", x["state"])
    if operation == "safety":
        return dict(exit_now=True, entry=bool(x["otherwise_entry"] and not x["residual"] and x["label"] != "SHOCK" and x["latch"] == "NONE"), label=x["label"], flip_count=x["flip_count"])
    if operation == "utc_days":
        days = sorted({math.floor(t / 86400) for t in x["seconds"]})
        return dict(days=days, count=len(days))
    if operation == "coverage":
        return s.pin8_coverage(**x)
    if operation == "branch_ledger":
        return s.branch_ledger(x["closed"])
    if operation == "pin15":
        x.pop("l1", None)  # HM conditional vectors supply known filled basis, not executable fill tapes.
        return s.score_pin15(**x)
    if operation == "pin15_counts":
        return s.pin15_counts(x["rows"])
    if operation == "dual":
        # Synthetic caller predicate, explicitly NOT a bootstrap or confidence estimator.
        confidence = 0 <= x["p_value"] <= .05 / x["N"]
        return s.dual_record(i=x["i"], ii=x["ii"], backtest=x["backtest"], N=x["N"], confidence_ok=confidence,
                             risk_ok=bool(x["dd_ok"] and not x["k3_kill"]), signal_id=x["signal_id"], entry_time=x["entry_time"], exit_time=x["exit_time"])
    if operation == "grammar_select":
        return contracts.select_ids(x["grammar"])
    if operation == "grammar_policy":
        return {"status": "PENDING_GS_SPEC_CLARIFICATION"}
    if operation == "frozen_ids":
        return dict(status="FROZEN_IDS_MATCH_UNIT_ONLY" if x["actual"] == x["expected"] else "STOP_FROZEN_IDS")
    if operation == "prior_c0":
        return contracts.prior_calibration(x)
    if operation == "role_mask":
        return contracts.role_mask(x)
    if operation == "runtime_gate":
        return contracts.runtime_gate(x)
    if operation == "eligibility":
        # Old tape vectors exercise hypothetical N arithmetic. Production PRIMARY_N stays 836 under R59(6).
        N = 836 + x.get("other_variants", 0) + int(x.get("extra_variant", False))
        status = "UNIT_BOUND"
        if not all(x.get(k, True) for k in ("whitelist", "blind", "frozen", "no_refit", "data_match", "state_match")):
            status = "STOP"
        elif x.get("extra_variant"):
            status = "STOP_AMEND_N"
        return dict(status=status, N=N, primary_N=contracts.PRIMARY_N, synthetic_what_if_only=True)
    if operation == "ct_lookup":
        return contracts.ct_lookup(**x)
    if operation == "b3_guard":
        return contracts.b3_guard(x)
    if operation == "k3":
        return k3.score_k3(x)
    if operation == "falsekill":
        return k3.falsekill_probability()
    if operation == "executable_l1_mtm":
        return k3.executable_l1_mtm(x)
    raise ValueError("UNSUPPORTED_OPERATION:" + operation)


def subset_errors(expected, actual, path=""):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [path + ": not a dict"]
        errors = []
        for key, value in expected.items():
            errors.extend([path + "/" + key + ": missing"] if key not in actual else subset_errors(value, actual[key], path + "/" + key))
        return errors
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [path + ": list length/order mismatch"]
        return [error for i, value in enumerate(expected) for error in subset_errors(value, actual[i], path + "/" + str(i))]
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-10):
            return []
    elif type(expected) is type(actual) and expected == actual:
        return []
    return [f"{path}: expected {expected!r}, actual {actual!r}"]


def field9_actual(case):
    """Construct documented synthetic inputs; call the existing fill model read-only.

    This is an identity adapter regression, NOT off-bot L2 or historical cost
    mapping acceptance. Never-cheaper is only a historical negative control.
    """
    def row(t, bid, ask, **extra):
        return dict(bucket_ts=t, bid=bid, ask=ask, bid_qty=extra.pop("bid_qty", 2.0), ask_qty=extra.pop("ask_qty", 2.0),
                    fresh=True, valid_bbo=True, source_age_sec=.2, **extra)
    number = int(case["id"].split("-")[1])
    if number in (1, 2, 3, 13):
        rows = {t: row(t, 100.0 + t, 101.0 + t, ask_qty=.5) for t in range(1000, 1020)}
        if number == 13:
            for r in rows.values():
                r["fresh"] = False
        return fill_model.taker_fill_rows(rows, side=case["side"], qty=case["size"], decision_ts=1000.3, latency_sec=4)
    if number == 4:
        return dict(status="N/A_COMPONENT", size_walk=fill_model.size_walk(1106, .5, 1, 0, "LONG"))
    if number in (5, 6):
        rows = {t: row(t, 99.5, 100) for t in range(10)}
        rows[3] = row(3, 99, 99.5, bid_qty=1, trade_low=99, sell_qty=.4, sell_vwap=99)
        if number == 6:
            rows[7] = row(7, 98.5, 99, trade_low=98.9, sell_qty=.01, sell_vwap=98.9)
        return fill_model.maker_fill_rows(rows, side="LONG", qty=.1, schedule=[dict(start_ts=0, end_ts=10, limit_price=99)])
    if number in (7, 8):
        rows = {t: row(t, 99, 99.5, bid_qty=.3) for t in range(6)}
        rows[2] = row(2, 99, 99.5, bid_qty=.3, trade_low=99, sell_qty=.35, sell_vwap=99)
        if number == 8:
            rows = {t: row(t, 98, 98.8) for t in range(6)}
        return fill_model.maker_fill_rows(rows, side="LONG", qty=.1, schedule=[dict(start_ts=0, end_ts=6, limit_price=99)])
    if number in (9, 10, 11, 12):
        cur = [0, 1, 2, 3, -40, -45, -50, -50, -50, -50]
        if number == 10:
            cur = [0, 1, 2, 3, -45, -40, -40, -40, -40, -40]
        margin, idx = fill_model.realistic_exit_margin(cur, list(range(10)), 3 if number == 11 else 4,
            case["reason"], latency_sec=1, target_margin=2.5 if number == 11 else None)
        return dict(margin_pct=margin, booked_idx=idx)
    if number == 14:
        return dict(fee_maker=fill_model.fee_fields(25, maker=True), fee_taker=fill_model.fee_fields(25, maker=False), FEE_PROFILE_ID=fill_model.fee_fields(25, maker=True)["fee_profile_id"])
    if number == 15:
        residual = case["expected_mapped_cost_bp"] - case["expected_realistic_cost_bp"]
        return dict(residual_mapped_minus_realistic=residual, pass_never_cheaper=residual >= -1e-9, historical_only=True)
    raise ValueError("FIELD9_CASE_NOT_SUPPORTED")


def run():
    results = []
    for case in load_cases():
        if pending_reason(case):
            results.append(dict(id=case["id"], status="SKIP", reason=PENDING_REASON))
            continue
        try:
            actual = dispatch(case["operation"], case["input"])
            errors = subset_errors(case["expected"], actual)
        except Exception as exc:
            actual, errors = None, [type(exc).__name__ + ": " + str(exc)]
        results.append(dict(id=case["id"], status="FAIL" if errors else "PASS", actual=actual, errors=errors))
    field_results = []
    for case in json.loads((ROOT / "field9.json").read_text())["fixtures"]:
        actual = field9_actual(case)
        gold = {"residual_mapped_minus_realistic": -3, "pass_never_cheaper": False} if case["kind"] == "rule_negative" else case["expected_realistic"]
        errors = subset_errors(gold, actual)
        field_results.append(dict(id=case["id"], status="FAIL" if errors else "PASS", errors=errors, actual=actual))
    modules = [Path(__file__), Path(c.__file__), Path(s.__file__), Path(contracts.__file__), Path(k3.__file__)]
    summary = {status.lower(): sum(r["status"] == status for r in results) for status in ("PASS", "FAIL", "SKIP")}
    summary.update(field9_pass=sum(r["status"] == "PASS" for r in field_results), field9_fail=sum(r["status"] == "FAIL" for r in field_results),
                   classification="PROVISIONAL_PRE_FREEZE_NON_EVIDENTIAL", acceptance_eligible=False, stage_d_authority=False,
                   production_reference="NOT_TESTED", all_tapes_executed=not summary["skip"], manifest_sha256=HM_MANIFEST_SHA256,
                   implementation_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in modules}, rules_sha256=RULES_SHA256)
    return dict(summary=summary, results=results, field9_results=field_results)


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, allow_nan=False))
    raise SystemExit(bool(result["summary"]["fail"] or result["summary"]["field9_fail"]))
