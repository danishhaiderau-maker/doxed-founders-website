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
    assert analyzer._joined_compressed_chase_tape_evidence({}, {}, [], "long", {}) == {}
    assert analyzer._joined_compressed_chase_tape_evidence({}, {}, [], "long", {}) == {}
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
