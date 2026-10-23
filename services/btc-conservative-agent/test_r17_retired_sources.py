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
