"""Language-neutral fixture adapter executing independent Pin implementation.

No HM oracle Python is imported or executed. Fixture expected values are read
only by the assertion harness, never passed to implementation entry points.
The CLI emits JSON on stdout only; all outputs are provisional/non-evidential.
"""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79"
P8_SPEC_SHA256 = "28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

import hashlib
import json
import math
from pathlib import Path
from . import p8_fixture_adapter as adapter
from . import pin_fill_operations as fills
from . import pin7_classifier as c
from . import pin_research_contracts as contracts
from . import pin_scoring as s
from . import pin_k3 as k3

ROOT = Path(__file__).with_name("pin_fixtures")
HM_MANIFEST_SHA256 = "fbdfd1ca2b6c69cf977b176f11ad1b1e9fad2f7782aaa01c30a69f159ec04c3e"
FIELD9_SHA256 = "39a9e5efe92e3cea97658ab445673cfc9928f8adda8fb2ec80c5afeaa97f1bd2"
THRESHOLDS = dict(v90=.02, v95=.03, e50=.30, e75=.60)  # SYNTHETIC ONLY
from .p8_fixture_adapter import evaluate, OPERATION_MAP


def pending_reason(case):
    return None  # Former GS semantic ambiguities are resolved in P8r10/Pin7r7.


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
    return evaluate(operation, data)


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
    if type(expected) is int:
        return [] if type(actual) is int and expected == actual else [f"{path}: integer type/value mismatch"]
    if isinstance(expected, float):
        if isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-10):
            return []
    elif type(expected) is type(actual) and expected == actual:
        return []
    return [f"{path}: expected {expected!r}, actual {actual!r}"]


def run():
    results = []
    for case in load_cases():
        try:
            actual = dispatch(case["operation"], case["input"])
            errors = subset_errors(case["expected"], actual)
        except Exception as exc:
            actual, errors = None, [type(exc).__name__ + ": " + str(exc)]
        results.append(dict(id=case["id"], status="FAIL" if errors else "PASS", actual=actual, errors=errors))
    modules = [Path(__file__), Path(c.__file__), Path(s.__file__), Path(contracts.__file__), Path(k3.__file__), Path(adapter.__file__), Path(fills.__file__)]
    summary = {status.lower(): sum(r["status"] == status for r in results) for status in ("PASS", "FAIL", "SKIP")}
    summary.update(
                   classification="PROVISIONAL_PRE_FREEZE_NON_EVIDENTIAL", acceptance_eligible=False, stage_d_authority=False,
                   production_reference="NOT_TESTED", all_tapes_executed=not summary["skip"], candidate_contract_hold=True, manifest_sha256=HM_MANIFEST_SHA256,
                   implementation_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in modules}, rules_sha256=RULES_SHA256)
    return dict(summary=summary, results=results)


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, allow_nan=False))
    raise SystemExit(bool(result["summary"]["fail"]))
