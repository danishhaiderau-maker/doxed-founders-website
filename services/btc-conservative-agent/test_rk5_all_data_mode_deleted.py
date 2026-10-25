"""RK5 (#882): ALL-DATA mode (data_scope="all") is deleted, not hidden."""
import inspect
from pathlib import Path

import analyzer_research_engine_v62 as analyzer
from research import research_dashboard as dashboard


def test_analyzer_has_no_all_data_scope_switch():
    for fn in (analyzer.run, analyzer.build_executive_summary_payload, analyzer.finalize_analyzer_outputs):
        params = inspect.signature(fn).parameters
        assert "data_scope" not in params and "session_only" not in params, fn.__name__
    assert not hasattr(analyzer, "generate_all_data_companion_reports")
    assert isinstance(analyzer.resolve_analyzer_session_scope(), str)


def test_dashboard_never_falls_back_to_all_data_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    name = "top_combinations_report.json"
    assert all("all_data" not in Path(p).parts for p in dashboard._data_file_candidates(name))
    current = tmp_path / dashboard.REPORTS_DIR / name
    stale = tmp_path / dashboard.ALL_DATA_REPORTS_DIR / name
    monkeypatch.setattr(dashboard, "_iter_data_payloads", lambda n: iter([
        (current, {"rows": []}, 1.0), (stale, {"rows": [{"x": 1}]}, 2.0)]))
    monkeypatch.setattr(dashboard, "_report_is_empty", lambda n, p: not p.get("rows"))
    monkeypatch.setattr(dashboard, "_scope_priority", lambda p, fresh_collection=False: 1 if p.get("rows") == [] else 0)
    assert dashboard._best_report_path(name) == current
