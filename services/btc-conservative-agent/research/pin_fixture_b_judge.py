"""PART B judge; plan r3 ce72bcd45975, rules 4305f12a02d2. Live OFF.

No real binder is distributed here. A separately reviewed gate preflight must
supply a trusted binder SHA on the CLI (not a digest learned from that file).
The binder JSON declares checkout, head, sources {checkout-relative: SHA256},
python {executable, version}, fixture_root, manifest_sha256, index_sha256,
mutations_sha256, supplement_root, supplement_path and supplement_sha256.
Source pins are external, including THIS judge and the unchanged fill closure.
Gate evidence is the separate preflight's responsibility, never an assertion
of approval inferred from this module or from the old supplement's head.

Invoke as a file in a fresh interpreter, not through an already-imported SUT:
python research/pin_fixture_b_judge.py --binder ... --binder-sha256 ... --runroot ...
Only run_part_b is the formal entry point. _attempt is a synthetic mini-packet
unit-test seam, not permission to execute real subsets or retry an attempt.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.machinery
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

SERVICE = "services/btc-conservative-agent/"
RESEARCH = SERVICE + "research/"
SOURCE_PATHS = frozenset(RESEARCH + name + ".py" for name in (
    "pin_fixture_b_judge", "p8_fixture_adapter", "pin7_classifier", "pin_scoring",
    "pin_k3", "pin_research_contracts", "pin_fill_operations", "fill_model",
)) | {SERVICE + "bitfinex_cost_profile.py"}
CONTROL_ID = "MUTANT-NOLOG-AUTO-RESTART-AND-RESCORE"
CONTROL_TARGET = "P8R10-12-NOLOG-TERMINAL-NO-AUTORESUME"


class JudgeError(ValueError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _contained(root, relative):
    root = Path(root).resolve(strict=True)
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise JudgeError("ROOT_ESCAPE:" + str(relative))
    path = (root / rel).resolve(strict=True)
    if not path.is_relative_to(root):
        raise JudgeError("ROOT_ESCAPE:" + str(relative))
    return path


def _read(root, relative, expected):
    data = _contained(root, relative).read_bytes()
    if _sha(data) != expected:
        raise JudgeError("HASH_MISMATCH:" + str(relative))
    return data


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JudgeError("DUPLICATE_JSON_KEY:" + key)
        result[key] = value
    return result


def _json(data):
    return json.loads(data, object_pairs_hook=_object,
                      parse_constant=lambda x: (_ for _ in ()).throw(JudgeError("NONFINITE_JSON:" + x)))


def _finite(value):
    if type(value) is float and not math.isfinite(value):
        raise JudgeError("NONFINITE_ACTUAL")
    if isinstance(value, dict):
        if any(type(k) is not str for k in value):
            raise JudgeError("NON_JSON_KEY")
        for v in value.values():
            _finite(v)
    elif isinstance(value, list):
        for v in value:
            _finite(v)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise JudgeError("NON_JSON_ACTUAL")


def _pointer(path, key):
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _compare(expected, actual, case_id, slots, path=""):
    if type(expected) is dict:
        if type(actual) is not dict:
            raise JudgeError(path + ": object type mismatch")
        for key, value in expected.items():
            if key not in actual:
                raise JudgeError(_pointer(path, key) + ": missing key")
            _compare(value, actual[key], case_id, slots, _pointer(path, key))
    elif type(expected) is list:
        if type(actual) is not list or len(expected) != len(actual):
            raise JudgeError(path + ": list length/type mismatch")
        for i, value in enumerate(expected):
            _compare(value, actual[i], case_id, slots, _pointer(path, i))
    elif type(expected) is int:
        if type(actual) is int and actual == expected:
            return
        if ((case_id, path) in slots and type(actual) is float
                and math.isfinite(actual) and actual.is_integer() and actual == expected):
            return
        raise JudgeError(path + ": integer type/value mismatch")
    elif type(expected) is float:
        if (type(actual) not in (int, float) or not math.isfinite(expected)
                or abs(actual - expected) > max(1e-10, 1e-9 * abs(expected))):
            raise JudgeError(path + ": float tolerance mismatch")
    elif type(actual) is not type(expected) or actual != expected:
        raise JudgeError(path + ": literal mismatch")


def compare_case(case, actual, slots, candidates):
    """One comparator for genuine cases AND the fault-injected output control.

    Extra diagnostics are allowed by subset semantics, but nonfinite diagnostics
    and any literal E outside [0,1] are never hidden by that subset comparison.
    """
    _finite(actual)
    _ranges(actual)
    if case["id"] in candidates:
        if (case["operation"] != "candidate" or type(case["expected"]) is not str
                or type(actual) is not dict or set(actual) != {"candidate"}
                or type(actual["candidate"]) is not str):
            raise JudgeError("CANDIDATE_SHAPE")
        actual = actual["candidate"]
    _compare(case["expected"], actual, case["id"], slots)


def _ranges(value):
    if isinstance(value, dict):
        if "E" in value and (type(value["E"]) not in (int, float) or not 0 <= value["E"] <= 1):
            raise JudgeError("E_RANGE")
        for child in value.values():
            _ranges(child)
    elif isinstance(value, list):
        for child in value:
            _ranges(child)


def _distinct(ids, label):
    if type(ids) is not list or any(type(i) is not str for i in ids) or len(set(ids)) != len(ids):
        raise JudgeError("DUPLICATE_OR_INVALID_IDS:" + label)
    return ids


def _packet(binding, counts, receipt):
    """Read no arbitrary supplement paths or archived actuals/oracles."""
    root = Path(binding["fixture_root"]).resolve(strict=True)
    manifest = _read(root, "MANIFEST.sha256", binding["manifest_sha256"])
    entries = {}
    for line in manifest.decode().splitlines():
        digest, relative = line.split(maxsplit=1)
        relative = relative.removeprefix("*")
        if relative in entries or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
            raise JudgeError("INVALID_MANIFEST")
        # Containment is checked for ALL names, but only approved data is read.
        _contained(root, relative)
        entries[relative] = digest
    for name, key in (("CASE-INDEX.json", "index_sha256"), ("MUTATIONS.json", "mutations_sha256")):
        if entries.get(name) != binding[key]:
            raise JudgeError("METADATA_PIN:" + name)
    index = _json(_read(root, "CASE-INDEX.json", binding["index_sha256"]))
    ordered = _distinct(index["B"], "B")
    a_ids = _distinct(index["A"], "A")
    if len(ordered) != counts[0] or set(a_ids) & set(ordered):
        raise JudgeError("CASE_COUNT_OR_OVERLAP")
    receipt["coverage_known"] = True
    receipt["results"] = [dict(id=i, status="MISSING") for i in ordered]
    files = _distinct(index["tape_files"], "tape_files")
    if index["B_required_judge_mutants"] != 1:
        raise JudgeError("CONTROL_COUNT")
    all_cases = []
    for name in files:
        # Only top-level tape arrays listed by CASE-INDEX and bound by MANIFEST.
        if Path(name).name != name:
            raise JudgeError("TAPE_NAME")
        relative = "tapes/" + name
        if relative not in entries:
            raise JudgeError("UNLISTED_TAPE")
        tape = _json(_read(root, relative, entries[relative]))
        if type(tape) is not list:
            raise JudgeError("TAPE_ARRAY")
        all_cases.extend(tape)
    tape_ids = _distinct([c["id"] for c in all_cases], "tapes")
    if set(tape_ids) != set(a_ids + ordered):
        raise JudgeError("MISSING_OR_EXTRA_TAPE_IDS")
    cases = {c["id"]: c for c in all_cases}
    supplement = _json(_read(binding["supplement_root"], binding["supplement_path"], binding["supplement_sha256"]))
    pairs = supplement["numeric_policy"]["cross_type_allowed_only_at"]
    slots = frozenset((p["case_id"], p["path"]) for p in pairs)
    candidates = _distinct(supplement["scalar_policy"]["case_ids"], "candidates")
    if (len(pairs) != counts[1] or len(slots) != counts[1] or len(candidates) != counts[2]
            or any(i not in ordered or type(p) is not str or not p.startswith("/") for i, p in slots)
            or not set(candidates) <= set(ordered)):
        raise JudgeError("SUPPLEMENT_COUNTS_OR_IDS")
    for i in candidates:
        if cases[i]["operation"] != "candidate" or type(cases[i]["expected"]) is not str:
            raise JudgeError("CANDIDATE_BINDING")
    mutation = _json(_read(root, "MUTATIONS.json", binding["mutations_sha256"]))["mutations"]
    if (len(mutation) != 1 or mutation[0]["id"] != CONTROL_ID
            or mutation[0]["case_id"] != CONTROL_TARGET or CONTROL_TARGET not in ordered
            or mutation[0]["must_be_rejected"] is not True or "candidate_output" not in mutation[0]):
        raise JudgeError("CONTROL_BINDING")
    return ordered, cases, slots, frozenset(candidates), mutation[0]


def _git_identity(checkout):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(checkout), *args], text=True, timeout=10).strip()
    return git("rev-parse", "HEAD"), git("status", "--porcelain", "--untracked-files=normal")


def _sources(binding):
    checkout = Path(binding["checkout"]).resolve(strict=True)
    if set(binding["sources"]) != SOURCE_PATHS:
        raise JudgeError("SOURCE_CLOSURE")
    if binding["python"] != {"executable": str(Path(sys.executable).resolve()), "version": sys.version}:
        raise JudgeError("INTERPRETER_BINDING")
    head, dirty = _git_identity(checkout)
    if head != binding["head"] or dirty:
        raise JudgeError("HEAD_OR_CLEAN_BINDING")
    for path, digest in binding["sources"].items():
        _read(checkout, path, digest)
    if Path(__file__).resolve() != _contained(checkout, RESEARCH + "pin_fixture_b_judge.py"):
        raise JudgeError("JUDGE_PATH_BINDING")
    return {"head": head, "sources": dict(binding["sources"]), "python": binding["python"]}


def _loaded_paths(binding):
    checkout = Path(binding["checkout"]).resolve(strict=True)
    research = _contained(checkout, RESEARCH)
    for name, module in tuple(sys.modules.items()):
        if name == "research":
            if set(Path(p).resolve() for p in module.__path__) != {research}:
                raise JudgeError("RESEARCH_PACKAGE_SHADOW")
        elif name.startswith("research.") or name == "bitfinex_cost_profile":
            filename = Path(module.__file__).resolve(strict=True)
            if not filename.is_relative_to(checkout) or filename.relative_to(checkout).as_posix() not in binding["sources"]:
                raise JudgeError("MODULE_SHADOW_OR_UNPINNED:" + name)


def _load_adapter(binding):
    # Fail closed, don't delete/replace preloaded modules or erase PYTHONPATH.
    if os.environ.get("PYTHONPATH"):
        raise JudgeError("PYTHONPATH_SHADOW_RISK")
    if any(n == "research" or n.startswith("research.") or n == "bitfinex_cost_profile" for n in sys.modules):
        raise JudgeError("SYS_MODULES_NOT_FRESH")
    service = _contained(binding["checkout"], SERVICE)
    for entry in sys.path:
        for name, desired in (("research", service / "research"), ("bitfinex_cost_profile", service / "bitfinex_cost_profile.py")):
            spec = importlib.machinery.PathFinder.find_spec(name, [entry or os.getcwd()])
            if spec is not None:
                found = ([Path(spec.origin).resolve()] if spec.origin else
                         [Path(p).resolve() for p in spec.submodule_search_locations])
                if found != [desired]:
                    raise JudgeError("SYS_PATH_SHADOW:" + name)
    sys.path.insert(0, str(service))
    importlib.invalidate_caches()
    adapter = importlib.import_module("research.p8_fixture_adapter")
    _loaded_paths(binding)
    return adapter.evaluate


def _seal(runroot, name, value):
    # Destinations belong solely to this newly claimed attempt. No old receipt
    # is ever replaced; .tmp is exclusively created and retained on seal errors.
    target = runroot / name
    if target.exists():
        raise JudgeError("SEALED_OUTPUT_EXISTS")
    with (runroot / (name + ".tmp")).open("x", encoding="utf-8") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(runroot / (name + ".tmp"), target)


def _timeout(*_):
    raise TimeoutError("WALL_300S")


def _attempt(binder_path, binder_sha256, runroot, *, counts, wall=300):
    runroot = Path(runroot).absolute()
    # mkdir itself is exclusive: no claim inside an existing/raced root.
    runroot.mkdir(parents=False, exist_ok=False)
    with (runroot / "STARTED").open("x", encoding="utf-8") as f:
        f.write("Consumed attempt. No reset, rename, reuse or retry.\n")
    receipt = dict(status="FAIL", results=[], executed=0, control_attempted=0,
                   control_rejected=0, binder_sha256=binder_sha256,
                   coverage_known=False, error=None, exit_code=1)
    ordered, binding, old_runroot = [], None, os.environ.get("RUNROOT")
    start = time.monotonic()
    old_handler = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, wall)
    try:
        binder_path = Path(binder_path).absolute()
        binding = _json(_read(binder_path.parent, binder_path.name, binder_sha256))
        ordered, cases, slots, candidates, mutation = _packet(binding, counts, receipt)
        receipt["coverage_known"] = True
        receipt["results"] = [dict(id=i, status="MISSING") for i in ordered]
        receipt["before"] = _sources(binding)
        evaluate = _load_adapter(binding)
        os.environ["RUNROOT"] = str(runroot)
        for i, case_id in enumerate(ordered):
            case = cases[case_id]
            row = receipt["results"][i]
            row["status"] = "FAIL"
            receipt["executed"] += 1
            # Only these TWO values cross the SUT boundary, never the envelope.
            actual = evaluate(case["operation"], case["input"])
            _finite(actual)  # Do not put non-JSON/nonfinite data in a sealed JSON.
            row["actual"] = actual
            compare_case(case, actual, slots, candidates)
            row["status"] = "PASS"
        receipt["control_attempted"] = 1
        try:
            compare_case(cases[mutation["case_id"]], mutation["candidate_output"], slots, candidates)
        except JudgeError as exc:
            receipt["control_rejected"] = 1
            receipt["control_reason"] = str(exc)
        else:
            raise JudgeError("CONTROL_WAS_ACCEPTED")
        _loaded_paths(binding)
        receipt["after"] = _sources(binding)
        _read(binder_path.parent, binder_path.name, binder_sha256)
        receipt.update(status="PASS", exit_code=0)
    except Exception as exc:
        receipt["error"] = type(exc).__name__ + ": " + str(exc)
        # Postflight still runs on comparator/import/setup failure, without
        # evaluating any further case or treating a loader error as rejection.
        if isinstance(exc, TimeoutError):
            receipt["postflight_error"] = "NOT_CHECKED_TIMEOUT"
        elif binding is not None:
            try:
                _loaded_paths(binding)
                receipt["after"] = _sources(binding)
                _read(binder_path.parent, binder_path.name, binder_sha256)
            except Exception as post:
                receipt["postflight_error"] = type(post).__name__ + ": " + str(post)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_runroot is None:
            os.environ.pop("RUNROOT", None)
        else:
            os.environ["RUNROOT"] = old_runroot
        receipt["elapsed_s"] = time.monotonic() - start
        receipt["missing"] = [r["id"] for r in receipt["results"] if r["status"] == "MISSING"]
        _seal(runroot, "receipt.json", receipt)
        _seal(runroot, "exit-code.json", receipt["exit_code"])
    return receipt


def run_part_b(binder_path, binder_sha256, runroot):
    """218 distinct B cases, unchanged 30 slots / 10 candidates, one control.

    Caller must first obtain the separate exact-head/byte/gate approvals. This
    function has no auto-generated bindings and confers no execution authority.
    """
    return _attempt(binder_path, binder_sha256, runroot, counts=(218, 30, 10))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binder", required=True)
    parser.add_argument("--binder-sha256", required=True)
    parser.add_argument("--runroot", required=True)
    args = parser.parse_args()
    receipt = run_part_b(args.binder, args.binder_sha256, args.runroot)
    return receipt["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
