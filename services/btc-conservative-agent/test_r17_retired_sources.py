"""RK #847 cond 4: tick-sim / 1s limit-fill producers are inventoried, retired and refused."""
import ast
import json
from pathlib import Path

import analyzer_research_engine_v62 as analyzer
from research import r3_quarantine

HERE = Path(__file__).resolve().parent
TICK_HELPERS = {"_simulate_ticks_fast_cut_ladder", "_sim_replay_pnl_usd", "_sim_replay_pnl_at_fill"}


def _callers(path: Path, names: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out = set()
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name in names:
            continue
        for node in ast.walk(fn):
            # any reference (direct call or ``evaluator = evaluate_limit_fill`` indirection)
            n = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if n in names:
                out.add(fn.name)
    return out


def test_inventory_matches_ast_and_counts_21():
    tick = _callers(HERE / "analyzer_research_engine_v62.py", TICK_HELPERS)
    assert tick == set(r3_quarantine.INVENTORY["TICK_SIM"])
    lf = set()
    names = {"evaluate_limit_fill", "_evaluate_conservative_limit_fill", "evaluate_conditional_limit_fill"}
    for p in [HERE / "analyzer_research_engine_v62.py", *sorted((HERE / "research").glob("*.py"))]:
        if p.name.startswith("test_") or p.name == "conservative_limit_fill.py":
            continue
        mod = "analyzer_research_engine_v62" if p.parent == HERE else f"research.{p.stem}"
        lf |= {f"{mod}.{c}" for c in _callers(p, names)}
    assert lf == set(r3_quarantine.INVENTORY["LIMIT_FILL_1S"])
    assert len(tick) + len(lf) == 21


def test_every_tick_sim_producer_is_retired_or_quarantined():
    for name in r3_quarantine.INVENTORY["TICK_SIM"]:
        fn = getattr(analyzer, name)
        if name == "exit_ladder_simulator_report":
            continue  # EXIT_LADDER_SIM early-return (#843)
        assert getattr(fn, "r3_retired_key", None) == "TICK_SIM", name


def test_retired_report_writers_write_nothing_and_label_section(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(analyzer, "DATA_DIR", str(tmp_path), raising=False)
    for name in ("qualified_exit_policy_grid_report", "fast_cut_survivor_report", "fast_cut_sweep_report"):
        rep = getattr(analyzer, name)()
        assert rep["status"] == r3_quarantine.STATUS and rep["countable"] is False
        assert rep["label"] == r3_quarantine.RETIRED_LABEL
    assert list(tmp_path.rglob("*.json")) == []
    out = capsys.readouterr().out
    assert out.count("source retired (R17)") == 3


def test_retired_sweeps_return_none_and_print_label(capsys):
    assert analyzer.margin_size_sweep_analysis(None, None) is None
    assert analyzer.thesis_fast_cut_optimization(None) is None
    assert "thesis_fast_cut_optimization: source retired (R17)" in capsys.readouterr().out


def test_readers_refuse_retired_files():
    for f in ("fast_cut_survivor_report.json", "fast_cut_sweep_report.json", "qualified_exit_policy_grid_report.json"):
        assert r3_quarantine.quarantined_file(f"/x/{f}")
    json.dumps(r3_quarantine.disabled_payload("TICK_SIM"))


# ---- LIMIT_FILL_1S (RK #847 cond 4): all six evaluate_limit_fill callers retired -----------------------------
import importlib

LIMIT_FILES = (
    "entry_baseline_replay_report.json",
    "entry_baseline_replay_episode_receipts.jsonl.gz",
    "conservative-results.jsonl.gz",
    "conservative_fill_descriptive_report.json",
)


def _resolve(dotted):
    mod, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(mod), name)


def test_every_limit_fill_1s_producer_is_retired():
    assert "LIMIT_FILL_1S" in r3_quarantine.ACTIVE
    for dotted in r3_quarantine.INVENTORY["LIMIT_FILL_1S"]:
        assert getattr(_resolve(dotted), "r3_retired_key", None) == "LIMIT_FILL_1S", dotted
    # the writers that wrap the producers are retired as well
    from research import entry_baseline_replay, policy_evidence_evaluator
    for fn in (entry_baseline_replay.materialize_same_opportunity_replay,
               entry_baseline_replay.materialize_v3_opportunity_replay,
               policy_evidence_evaluator.persist_v3_conservative_results):
        assert fn.r3_retired_key == "LIMIT_FILL_1S"


def test_limit_fill_1s_writers_write_nothing(tmp_path, capsys):
    from research import conservative_fill_cohort, entry_baseline_replay, policy_evidence_evaluator
    for rep in (entry_baseline_replay.materialize_v3_opportunity_replay(tmp_path),
                entry_baseline_replay.materialize_same_opportunity_replay(tmp_path),
                policy_evidence_evaluator.build_v3_conservative_results(tmp_path),
                policy_evidence_evaluator.persist_v3_conservative_results(tmp_path),
                conservative_fill_cohort.build_conservative_fill_cohort([]),
                conservative_fill_cohort.build_v3_conservative_fill_cohort([])):
        assert r3_quarantine.is_retired_payload(rep) and rep["countable"] is False
        assert rep["quarantine"] == "LIMIT_FILL_1S" and set(LIMIT_FILES) == set(rep["files_not_written"])
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().out.count("source retired (R17)") == 6


def test_limit_fill_1s_row_producers_return_no_evidence_and_print_once(capsys):
    from research import entry_baseline_replay, latency_schedule_replay
    for _ in range(2):
        ev = analyzer._joined_compressed_chase_tape_evidence({}, {}, [], "long", {})
        assert ev["outcome_code"] == "SOURCE_RETIRED_R17" and ev["coverage_status"] == r3_quarantine.RETIRED_LABEL
        assert ev["conservative_execution_supported"] is False and ev["countable"] is False
    assert entry_baseline_replay.replay_episode({}) is None
    out = latency_schedule_replay._replay_delayed_entry(
        schedule={}, delay_sec=1, ordering_treatment=None, tape=[], direction="long", requested_qty=None,
        quantity_constraints=None, symbol=None, conditional=False)
    assert out["status"] == "UNKNOWN" and out["reason_codes"] == ["SOURCE_RETIRED_R17"]
    assert out["qualification_eligible"] is False and out["entry_receipt"] is None
    assert capsys.readouterr().out.count("_joined_compressed_chase_tape_evidence: source retired (R17)") == 1


def test_limit_fill_1s_readers_refuse(tmp_path):
    from research import input_blockers, research_dashboard
    for f in LIMIT_FILES:
        assert r3_quarantine.quarantined_file(f"/x/{f}")
    (tmp_path / "entry_baseline_replay_report.json").write_text('{"schema":"stale"}')
    assert analyzer._load_json_report(str(tmp_path / "entry_baseline_replay_report.json")) == {}
    item = input_blockers.entry_baseline_replay_item(tmp_path)
    assert item["status"] == input_blockers.RETIRED and item["reason_code"] == input_blockers.SOURCE_RETIRED_R17
    assert research_dashboard._pick_best_payload("conservative_fill_descriptive_report.json", {"d": 1}) == {"d": 1}
    payload, _meta = research_dashboard._declared_atomic_generation_report("entry_baseline_replay_report.json")
    assert payload is None


def _row_pricer_callers() -> set[str]:
    names = set(r3_quarantine.FILL_MODEL_ROW_PRICERS)
    out = set()
    for p in [*sorted(HERE.glob("*.py")), *sorted((HERE / "research").glob("*.py"))]:
        if p.name.startswith("test_") or p.name == "fill_model.py":
            continue
        mod = p.stem if p.parent == HERE else f"research.{p.stem}"
        out |= {f"{mod}.{c}" for c in _callers(p, names)}
    return out


def test_fill_model_row_pricer_callers_are_pinned_and_quarantined():
    """HM #850 R2 C1: fill_model.taker_fill_rows / maker_fill_rows / realistic_exit_margin price on the 1s tape."""
    assert _row_pricer_callers() == set(r3_quarantine.FILL_MODEL_ROW_CALLERS)
    for caller, key in r3_quarantine.FILL_MODEL_ROW_CALLERS.items():
        assert key in r3_quarantine.QUARANTINE and r3_quarantine.is_active(key), caller
    from research import genome_grid_study, walk_forward_scorer
    for fn in ("simulate_entry", "simulate_exit", "simulate", "run"):
        assert getattr(walk_forward_scorer, fn).r3_retired_key == "WALK_FORWARD_1S", fn
    assert 'r3_quarantine.guard("GENOME_GRID"' in Path(genome_grid_study.__file__).read_text(encoding="utf-8")


def test_walk_forward_scorer_retired_writes_nothing(tmp_path, capsys):
    from research import walk_forward_scorer as wfs
    rep = wfs.run(tmp_path / "t.csv.gz", tmp_path, tmp_path / "out")
    assert r3_quarantine.is_retired_payload(rep) and rep["quarantine"] == "WALK_FORWARD_1S"
    assert set(rep["files_not_written"]) == {"walk_forward_scores.json", "walk_forward_scores.md",
                                             "walk_forward_trades.jsonl.gz"}
    assert wfs.simulate([], None, latency_sec=1.0) == []
    leg = wfs.simulate_entry(None, {}, {}, "LONG", None, 1.0)
    assert leg["status"] == "SOURCE_RETIRED_R17" and leg["label"] == r3_quarantine.RETIRED_LABEL
    assert wfs.main(["--table", str(tmp_path / "t"), "--data-dir", str(tmp_path), "--out-dir",
                     str(tmp_path / "out")]) == 0
    assert list(tmp_path.iterdir()) == []
    assert "source retired (R17)" in capsys.readouterr().out


# ---- HM #850 R3: repo-wide pin of every retired-tape-pricer reference ------------------------------------------
REPO = HERE.parents[1]
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "tests", ".pytest_cache"}
_SKIP_FILES = {"fill_model.py", "r3_quarantine.py", "conftest.py"}


def _docstring_ids(tree) -> set[int]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _refs(node, names, docs) -> set[str]:
    hit = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in names:
            hit.add(n.id)
        elif isinstance(n, ast.Attribute) and n.attr in names:
            hit.add(n.attr)
        elif isinstance(n, ast.alias) and (n.name.rsplit(".", 1)[-1] in names or n.asname in names):
            hit.add(n.name)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            hit |= {x for x in names if x in n.value}  # getattr(fm, "realistic_exit_margin") style indirection
    return hit


def _scopes(tree, names):
    """(scope, node) units: top-level functions, class methods / class-level code, module-level statements.  A
    function named after a retired pricer is that pricer's own definition and is not a caller."""
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if st.name not in names:
                yield st.name, st
        elif isinstance(st, ast.ClassDef):
            for b in st.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if b.name not in names:
                        yield f"{st.name}.{b.name}", b
                else:
                    yield f"{st.name}.<class>", b
        else:
            yield "<module>", st


def _repo_pricer_references() -> dict[str, set[str]]:
    names = set(r3_quarantine.RETIRED_TAPE_PRICERS)
    out: dict[str, set[str]] = {}
    for p in sorted(REPO.rglob("*.py")):
        rel = p.relative_to(REPO)
        if _SKIP_DIRS & set(rel.parts[:-1]) or p.name in _SKIP_FILES:
            continue
        if p.name.startswith("test_") or p.name.endswith("_test.py"):
            continue
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(rel))  # unparseable = fail closed
        docs = _docstring_ids(tree)
        for scope, node in _scopes(tree, names):
            hit = _refs(node, names, docs)
            if hit:
                out.setdefault(f"{rel.as_posix()}::{scope}", set()).update(hit)
    return out


def test_repo_wide_retired_pricer_references_are_pinned_and_quarantined():
    """HM #850 R3: whole repo (minus tests / fill_model.py / the registry), every scope kind and string constants."""
    found = _repo_pricer_references()
    assert set(found) == set(r3_quarantine.REPO_PRICER_REFERENCES), (
        sorted(set(found) ^ set(r3_quarantine.REPO_PRICER_REFERENCES)))
    for scope, key in r3_quarantine.REPO_PRICER_REFERENCES.items():
        assert key in r3_quarantine.QUARANTINE and r3_quarantine.is_active(key), scope
    # the narrower fill_model pin is a subset of the repo-wide one
    fm_names = set(r3_quarantine.FILL_MODEL_ROW_PRICERS)
    fm_scopes = {s for s, h in found.items() if h & fm_names}
    assert "diagnostics/hypothesis-tiles-20261004/exit_study.py::replay" in fm_scopes
    assert len(fm_scopes) == 6  # 4 R2 callers + genome_grid canonical_parity_sample label + exit_study.replay


def test_scanner_catches_every_scope_kind(tmp_path):
    names = set(r3_quarantine.RETIRED_TAPE_PRICERS)
    src = (
        '"""docstring naming realistic_exit_margin is ignored"""\n'
        "from research.fill_model import taker_fill_rows\n"
        "X = fm.maker_fill_rows\n"
        "class K:\n"
        "    P = 'realistic_exit_margin'\n"
        "    def m(self):\n"
        "        return getattr(fm, 'taker_fill_rows')\n"
        "def f():\n"
        "    def inner():\n"
        "        return fm.realistic_exit_margin\n"
        "    return inner\n"
        "def g():\n"
        '    """mentions evaluate_limit_fill only in a docstring"""\n'
        "    return 1\n"
    )
    tree = ast.parse(src)
    docs = _docstring_ids(tree)
    got = {scope for scope, node in _scopes(tree, names) if _refs(node, names, docs)}
    assert got == {"<module>", "K.<class>", "K.m", "f"}


def _load_exit_study():
    import importlib.util
    path = REPO / "diagnostics" / "hypothesis-tiles-20261004" / "exit_study.py"
    spec = importlib.util.spec_from_file_location("exit_study_r17_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_exit_study_refuses_source_retired_r17(tmp_path, capsys, monkeypatch):
    import pytest
    es = _load_exit_study()
    out = tmp_path / "study.json"
    monkeypatch.setattr("sys.argv", ["exit_study.py", "--mirror", str(tmp_path), "--tier-a", str(tmp_path),
                                     "--out", str(out)])
    assert es.main() == es.RETIRED_EXIT_CODE != 0
    err = capsys.readouterr().err
    assert "SOURCE_RETIRED_R17" in err and r3_quarantine.RETIRED_LABEL in err
    assert not out.exists() and list(tmp_path.iterdir()) == []
    with pytest.raises(r3_quarantine.R3QuarantinedSource, match="SOURCE_RETIRED_R17 EXIT_STUDY_1S"):
        es.replay({"cur": [], "mfe": [], "age": []}, {}, 0.0)
