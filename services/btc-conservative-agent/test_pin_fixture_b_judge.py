"""Synthetic mini-packets ONLY. No frozen-tape SUT calls / acceptance run."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from research import pin_fixture_b_judge as j


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def case(expected, operation="unit", ident="c"):
    return dict(id=ident, operation=operation, input={}, expected=expected)


@pytest.mark.parametrize("expected,actual,allowed,passes", [
    (3, 3, False, True), (3, 3., True, True), (3, 3, True, True),
    (3, 3., False, False), (3, 3.000000000000001, True, False),
    (3, True, True, False), (3, "3", True, False), (0, -0., True, True),
    (9007199254740993, 9007199254740992., True, False),
    (1., 1 + 1e-10, False, True), (1., 1 + 2e-9, False, False),
    (0., 1e-10, False, True), (0., 1.00001e-10, False, False),
    (1., 1, False, True), (1., True, False, False),
    (True, 1, False, False), (None, None, False, True),
    ("UP", "up", False, False), ([1, 2], [2, 1], False, False),
    ([1], [1, 2], False, False), ({"n": 1}, {}, False, False),
    ({"n": 1}, {"n": 1, "extra": 3}, False, True),
])
def test_r3_comparator_exact_contract(expected, actual, allowed, passes):
    # Root slot is synthetic; real slots are nonempty JSON pointers.
    slots = {("c", "")} if allowed else set()
    if passes:
        j.compare_case(case(expected), actual, slots, set())
    else:
        with pytest.raises(j.JudgeError):
            j.compare_case(case(expected), actual, slots, set())


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_r3_global_nonfinite_in_unasserted_diagnostics(number):
    with pytest.raises(j.JudgeError, match="NONFINITE"):
        j.compare_case(case({"n": 1}), {"n": 1, "hidden": [{"x": number}]}, set(), set())


@pytest.mark.parametrize("actual", [{"candidate": "UP", "extra": True}, "UP",
                                   {"value": "UP"}, {"candidate": 1}, {"candidate": "DOWN"}])
def test_r3_candidate_only_exact_wrapper(actual):
    with pytest.raises(j.JudgeError):
        j.compare_case(case("UP", "candidate"), actual, set(), {"c"})


def test_r3_candidate_projection_scoped_and_pointer_escaped():
    j.compare_case(case("UP", "candidate"), {"candidate": "UP"}, set(), {"c"})
    with pytest.raises(j.JudgeError):
        j.compare_case(case("UP", "candidate"), {"candidate": "UP"}, set(), set())
    j.compare_case(case({"a/b~": [3]}), {"a/b~": [3.]}, {("c", "/a~1b~0/0")}, set())
    with pytest.raises(j.JudgeError):
        j.compare_case(case({"a/b~": [3]}), {"a/b~": [3.]}, {("other", "/a~1b~0/0")}, set())
    with pytest.raises(j.JudgeError, match="E_RANGE"):
        j.compare_case(case({}), {"E": 1.000000000000001}, set(), set())


@pytest.fixture
def mini(tmp_path, monkeypatch):
    root = tmp_path / "packet"
    root.mkdir()
    (root / "tapes").mkdir()
    tape = [dict(case({"n": 2}, ident="first"), input={"answer": {"n": 2.}}),
            dict(case({"latched": True}, ident=j.CONTROL_TARGET), input={"answer": {"latched": True}}),
            case({}, ident="partA")]
    index = dict(A=["partA"], B=["first", j.CONTROL_TARGET],
                 tape_files=["unit.json"], B_required_judge_mutants=1)
    supplement = dict(numeric_policy={"cross_type_allowed_only_at": [{"case_id": "first", "path": "/n"}]},
                      scalar_policy={"case_ids": []}, dispositions_path="/must/not/read")
    mutation = dict(mutations=[dict(id=j.CONTROL_ID, case_id=j.CONTROL_TARGET,
                                   candidate_output={"latched": False}, must_be_rejected=True)])
    binder = dict(fixture_root=str(root), supplement_root=str(tmp_path),
                  supplement_path="supplement.json", sources={})
    path = tmp_path / "binder.json"
    calls = []
    def evaluate(operation, data):
        calls.append((operation, data))
        return data["answer"]
    monkeypatch.setattr(j, "_sources", lambda _: {"pinned": True})
    monkeypatch.setattr(j, "_loaded_paths", lambda _: None)
    monkeypatch.setattr(j, "_load_adapter", lambda _: evaluate)
    def build():
        put(root / "tapes/unit.json", tape)
        put(root / "CASE-INDEX.json", index)
        put(root / "MUTATIONS.json", mutation)
        put(tmp_path / "supplement.json", supplement)
        lines = [sha(root / f) + "  " + f for f in ("CASE-INDEX.json", "MUTATIONS.json", "tapes/unit.json")]
        (root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
        binder.update(manifest_sha256=sha(root / "MANIFEST.sha256"),
                      index_sha256=sha(root / "CASE-INDEX.json"),
                      mutations_sha256=sha(root / "MUTATIONS.json"),
                      supplement_sha256=sha(tmp_path / "supplement.json"))
        put(path, binder)
        return sha(path)
    def run(digest=None, **kwargs):
        return j._attempt(path, digest or sha(path), tmp_path / "attempt", counts=(2, 1, 0), **kwargs)
    build()
    return dict(root=root, tmp=tmp_path, tape=tape, index=index, supplement=supplement,
                mutation=mutation, binder=binder, path=path, build=build, run=run, calls=calls)


def test_r3_run_once_order_control_and_two_argument_boundary(mini):
    # Packet order need not equal execution order: CASE-INDEX owns order.
    mini["tape"].reverse()
    mini["build"]()
    receipt = mini["run"]()
    assert receipt["status"] == "PASS" and receipt["exit_code"] == 0
    assert receipt["executed"] == 2 and not receipt["missing"]
    assert [r["id"] for r in receipt["results"]] == mini["index"]["B"]
    assert receipt["control_attempted"] == receipt["control_rejected"] == 1
    assert len(mini["calls"]) == 2
    assert all(set(data) == {"answer"} for _, data in mini["calls"])
    runroot = mini["tmp"] / "attempt"
    before = {p.name: p.read_bytes() for p in runroot.iterdir()}
    with pytest.raises(FileExistsError):
        mini["run"]()
    assert before == {p.name: p.read_bytes() for p in runroot.iterdir()}
    assert json.loads((runroot / "receipt.json").read_text()) == receipt
    assert json.loads((runroot / "exit-code.json").read_text()) == 0


@pytest.mark.parametrize("bad", [{"n": 3}, {"n": "2"}, {}, {"n": True}, {"n": float("nan")}])
def test_r3_first_failure_prefix_and_missing(mini, bad):
    mini["tape"][0]["input"]["answer"] = bad
    # Standard JSON parser rejects NaN at setup, before any SUT call.
    mini["build"]()
    receipt = mini["run"]()
    assert receipt["status"] == "FAIL" and receipt["control_attempted"] == 0
    if bad.get("n") != bad.get("n"):
        assert receipt["executed"] == 0 and receipt["missing"] == mini["index"]["B"]
    else:
        assert receipt["executed"] == 1 and len(mini["calls"]) == 1
        assert [r["status"] for r in receipt["results"]] == ["FAIL", "MISSING"]
        assert receipt["missing"] == [j.CONTROL_TARGET]


def test_r3_later_failure_keeps_pass_prefix(mini):
    mini["tape"][1]["input"]["answer"]["latched"] = False
    mini["build"]()
    out = mini["run"]()
    assert [r["status"] for r in out["results"]] == ["PASS", "FAIL"]
    assert out["executed"] == 2 and out["control_attempted"] == 0


@pytest.mark.parametrize("change", ["duplicate_tape", "missing", "extra", "duplicate_index", "wrong_count"])
def test_r3_ids_preflight_before_any_sut(mini, change):
    if change == "duplicate_tape":
        mini["tape"].append(mini["tape"][0])
    elif change == "missing":
        mini["tape"].pop(0)
    elif change == "extra":
        mini["tape"].append(case({}, ident="intruder"))
    elif change == "duplicate_index":
        mini["index"]["B"][1] = "first"
    else:
        mini["index"]["B"].pop()
    mini["build"]()
    out = mini["run"]()
    assert out["status"] == "FAIL" and not mini["calls"] and out["executed"] == 0


@pytest.mark.parametrize("file", ["MANIFEST.sha256", "CASE-INDEX.json", "MUTATIONS.json", "tapes/unit.json"])
def test_r3_all_packet_reads_hash_bound(mini, file):
    path = mini["root"] / file
    path.write_text(path.read_text() + " ")
    out = mini["run"]()
    assert out["status"] == "FAIL" and "HASH_MISMATCH" in out["error"]
    assert not mini["calls"]


def test_r3_binder_and_supplement_hash_binding(mini):
    out = mini["run"]("0" * 64)
    assert "HASH_MISMATCH" in out["error"] and out["executed"] == 0
    assert (mini["tmp"] / "attempt/STARTED").exists()
    with pytest.raises(FileExistsError):
        mini["run"]()


def test_r3_supplement_hash_mismatch(mini):
    (mini["tmp"] / "supplement.json").write_text("{}")
    assert "HASH_MISMATCH" in mini["run"]()["error"]
    assert not mini["calls"]


def test_r3_read_surface_never_follows_dispositions(mini, monkeypatch):
    original = Path.read_bytes
    reads = []
    def guarded(path):
        reads.append(path)
        assert "must/not/read" not in str(path)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", guarded)
    assert mini["run"]()["status"] == "PASS"
    assert all(p.name in {"binder.json", "MANIFEST.sha256", "CASE-INDEX.json", "MUTATIONS.json",
                          "unit.json", "supplement.json"} for p in reads)


@pytest.mark.parametrize("kind", ["parent", "absolute", "symlink"])
def test_r3_root_containment(mini, kind):
    outside = mini["tmp"] / "outside.json"
    outside.write_text("[]")
    if kind == "symlink":
        (mini["root"] / "escape.json").symlink_to(outside)
        relative = "escape.json"
    else:
        relative = "../outside.json" if kind == "parent" else str(outside)
    manifest = mini["root"] / "MANIFEST.sha256"
    manifest.write_text(manifest.read_text() + sha(outside) + "  " + relative + "\n")
    mini["binder"]["manifest_sha256"] = sha(manifest)
    put(mini["path"], mini["binder"])
    out = mini["run"]()
    assert "ROOT_ESCAPE" in out["error"] and not mini["calls"]


@pytest.mark.parametrize("kind", ["accepted", "bad_loader", "duplicate", "target"])
def test_r3_control_must_reject_not_loader_error(mini, kind):
    mutation = mini["mutation"]["mutations"][0]
    if kind == "accepted":
        mutation["candidate_output"] = {"latched": True}
    elif kind == "bad_loader":
        mutation.pop("candidate_output")
    elif kind == "duplicate":
        mini["mutation"]["mutations"].append(dict(mutation))
    else:
        mutation["case_id"] = "first"
    mini["build"]()
    out = mini["run"]()
    assert out["status"] == "FAIL" and out["control_rejected"] == 0
    if kind == "accepted":
        assert out["executed"] == 2 and out["control_attempted"] == 1
        assert "CONTROL_WAS_ACCEPTED" in out["error"]
    else:
        assert out["executed"] == 0 and out["control_attempted"] == 0


def test_r3_exception_and_timeout_consume_attempt(mini, monkeypatch):
    def stalled(*_):
        # Simulates a long SUT without sleeping. SIGALRM interrupts this loop.
        while True:
            pass
    monkeypatch.setattr(j, "_load_adapter", lambda _: stalled)
    out = mini["run"](wall=.02)
    assert out["status"] == "FAIL" and "TimeoutError" in out["error"]
    assert out["executed"] == 1 and out["missing"] == [j.CONTROL_TARGET]
    assert out["control_attempted"] == 0
    with pytest.raises(FileExistsError):
        mini["run"]()


def test_r3_import_error_is_not_control_rejection(mini, monkeypatch):
    def fail(_):
        raise ImportError("synthetic")
    monkeypatch.setattr(j, "_load_adapter", fail)
    out = mini["run"]()
    assert out["status"] == "FAIL" and "ImportError" in out["error"]
    assert out["executed"] == out["control_attempted"] == out["control_rejected"] == 0
    assert out["missing"] == mini["index"]["B"]


def test_r3_postflight_source_change_blocks_pass(mini, monkeypatch):
    calls = []
    def sources(_):
        calls.append(1)
        if len(calls) > 1:
            raise j.JudgeError("HASH_MISMATCH:source")
        return {"source": "trusted"}
    monkeypatch.setattr(j, "_sources", sources)
    out = mini["run"]()
    assert out["status"] == "FAIL" and "HASH_MISMATCH" in out["error"]
    assert out["executed"] == 2  # Honest coverage, not a PASS despite all rows passing.


def test_r3_formal_entry_fixed_cardinalities(mini, monkeypatch):
    calls = []
    monkeypatch.setattr(j, "_attempt", lambda *a, **kw: calls.append(kw))
    j.run_part_b(mini["path"], sha(mini["path"]), mini["tmp"] / "formal-not-created")
    assert calls == [{"counts": (218, 30, 10)}]


def synthetic_sources(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for relative in j.SOURCE_PATHS:
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic source only\n")
    return dict(checkout=str(checkout), head="trusted-new-head",
                sources={p: sha(checkout / p) for p in j.SOURCE_PATHS},
                python={"executable": str(Path(sys.executable).resolve()), "version": sys.version})


@pytest.mark.parametrize("change", ["none", "source", "head", "dirty", "closure", "python", "judge"])
def test_r3_j2_trusted_source_pins_not_self_learned(tmp_path, monkeypatch, change):
    b = synthetic_sources(tmp_path)
    checkout = Path(b["checkout"])
    monkeypatch.setattr(j, "__file__", str(checkout / j.RESEARCH / "pin_fixture_b_judge.py"))
    monkeypatch.setattr(j, "_git_identity", lambda _: (b["head"], ""))
    if change == "source":
        (checkout / j.RESEARCH / "fill_model.py").write_text("# changed after trusted binder\n")
    elif change == "head":
        monkeypatch.setattr(j, "_git_identity", lambda _: ("other", ""))
    elif change == "dirty":
        monkeypatch.setattr(j, "_git_identity", lambda _: (b["head"], " M changed"))
    elif change == "closure":
        b["sources"].pop(j.SERVICE + "bitfinex_cost_profile.py")
    elif change == "python":
        b["python"]["version"] = "other"
    elif change == "judge":
        monkeypatch.setattr(j, "__file__", str(tmp_path / "other.py"))
    if change == "none":
        assert j._sources(b)["sources"] == b["sources"]
    else:
        with pytest.raises(j.JudgeError):
            j._sources(b)


@pytest.mark.parametrize("change", ["none", "modules", "pythonpath", "sys_path", "loaded_escape"])
def test_r3_j2_fresh_package_import_and_shadow_refusal(tmp_path, change):
    b = synthetic_sources(tmp_path)
    checkout = Path(b["checkout"])
    adapter = checkout / j.RESEARCH / "p8_fixture_adapter.py"
    adapter.write_text("def evaluate(operation, input):\n    return input\n")
    b["sources"][j.RESEARCH + "p8_fixture_adapter.py"] = sha(adapter)
    path = tmp_path / "b.json"
    put(path, b)
    # Fresh interpreter is essential: pytest itself has imported research.
    script = """
import importlib.util, json, os, pathlib, sys, types
spec=importlib.util.spec_from_file_location('judge_subject', sys.argv[1])
j=importlib.util.module_from_spec(spec); spec.loader.exec_module(j)
b=json.loads(pathlib.Path(sys.argv[2]).read_text()); change=sys.argv[3]
if change=='modules': sys.modules['research.pin7_classifier']=types.ModuleType('shadow')
if change=='pythonpath': os.environ['PYTHONPATH']='/untrusted'
if change=='sys_path':
    shadow=pathlib.Path(sys.argv[2]).parent/'shadow'; (shadow/'research').mkdir(parents=True)
    (shadow/'research/__init__.py').write_text('')
    sys.path.insert(0,str(shadow))
try:
    evaluate=j._load_adapter(b)
    assert evaluate('unit', {'only_input': 1})=={'only_input':1}
    if change=='loaded_escape':
        m=types.ModuleType('research.shadow'); m.__file__=sys.argv[2]
        sys.modules[m.__name__]=m; j._loaded_paths(b)
except j.JudgeError:
    assert change!='none'
    print('REFUSED')
else:
    assert change=='none'
    print('BOUND')
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-B", "-c", script, j.__file__, str(path), change],
                            capture_output=True, text=True, env=env, timeout=10, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("BOUND" if change == "none" else "REFUSED")


@pytest.mark.parametrize("change", ["duplicate_slot", "foreign_slot", "extra_candidate", "metadata_pin"])
def test_r3_supplement_and_metadata_preflight(mini, change):
    slots = mini["supplement"]["numeric_policy"]["cross_type_allowed_only_at"]
    if change == "duplicate_slot":
        slots.append(dict(slots[0]))
    elif change == "foreign_slot":
        slots[0]["case_id"] = "partA"
    elif change == "extra_candidate":
        mini["supplement"]["scalar_policy"]["case_ids"] = ["first"]
    mini["build"]()
    if change == "metadata_pin":
        mini["binder"]["index_sha256"] = "0" * 64
        put(mini["path"], mini["binder"])
    out = mini["run"]()
    assert out["status"] == "FAIL" and not mini["calls"]


@pytest.mark.parametrize("failure", ["exception", "nonfinite", "nonjson"])
def test_r3_sut_error_seals_fail_prefix_without_invalid_json(mini, monkeypatch, failure):
    def bad(*_):
        if failure == "exception":
            raise ValueError("synthetic SUT failure")
        return {"hidden": float("inf") if failure == "nonfinite" else object()}
    monkeypatch.setattr(j, "_load_adapter", lambda _: bad)
    out = mini["run"]()
    assert out["status"] == "FAIL" and out["executed"] == 1
    assert out["missing"] == [j.CONTROL_TARGET] and out["control_attempted"] == 0
    assert "actual" not in out["results"][0]
    assert json.loads((mini["tmp"] / "attempt/exit-code.json").read_text()) == 1


def test_r3_binder_change_during_execution_not_pass(mini, monkeypatch):
    def changed(operation, data):
        mini["path"].write_text("{}")
        return data["answer"]
    monkeypatch.setattr(j, "_load_adapter", lambda _: changed)
    out = mini["run"]()
    assert out["status"] == "FAIL" and "HASH_MISMATCH" in out["error"]


def test_r3_existing_root_refused_even_without_started(mini):
    root = mini["tmp"] / "attempt"
    root.mkdir()
    (root / "do-not-overwrite").write_text("untouched")
    with pytest.raises(FileExistsError):
        mini["run"]()
    assert [p.name for p in root.iterdir()] == ["do-not-overwrite"]


def test_r3_json_duplicate_keys_rejected():
    with pytest.raises(j.JudgeError, match="DUPLICATE_JSON_KEY"):
        j._json('{"B": [], "B": []}')
