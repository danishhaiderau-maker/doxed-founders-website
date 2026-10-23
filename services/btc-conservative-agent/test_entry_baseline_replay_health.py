"""An all-UNKNOWN / zero-fill entry baseline replay is DEGRADED in the analyzer, never OK."""
import json
from pathlib import Path

from research import input_blockers as ib
from research.entry_baseline_replay import replay_health

HERE = Path(__file__).resolve().parent


def _summary(evals, unknown, full=0, partial=0, no=0):
    return {"opportunities": evals, "directional_evaluations": evals, "full_fills": full,
            "partial_fills": partial, "no_fills": no, "unknown": unknown}


def _report(**summaries):
    return {"same_opportunity_count": 7, "summaries": summaries}


def test_all_unknown_zero_fills_is_degraded():
    health = replay_health(_report(a=_summary(14, 14), b=_summary(14, 14)))
    assert health["status"] == "DEGRADED"
    assert health["reason_code"] == "BASELINE_REPLAY_ALL_UNKNOWN_ZERO_FILLS"
    assert health["fills"] == 0 and health["unknown"] == 28


def test_no_evaluations_is_degraded_and_garbage_never_raises():
    assert replay_health(_report())["status"] == "DEGRADED"
    assert replay_health(None)["reason_code"] == "BASELINE_REPLAY_NO_EVALUATIONS"
    assert replay_health({"summaries": {"a": "junk", "b": {"unknown": "x"}}})["status"] == "DEGRADED"


def test_measured_outcomes_are_ok():
    assert replay_health(_report(a=_summary(10, 9, full=1)))["status"] == "OK"
    assert replay_health(_report(a=_summary(10, 4, no=6)))["status"] == "OK"  # no-fill is evidence


def test_input_blockers_item_degrades_the_level(tmp_path):
    (tmp_path / ib.ENTRY_BASELINE_REPLAY_REPORT).write_text(json.dumps(_report(a=_summary(3, 3))))
    item = ib.entry_baseline_replay_item(tmp_path)
    assert item["status"] == "DEGRADED" and item["reason_code"] == "BASELINE_REPLAY_ALL_UNKNOWN_ZERO_FILLS"
    (tmp_path / ib.ENTRY_BASELINE_REPLAY_REPORT).write_text(json.dumps(_report(a=_summary(3, 1, full=2))))
    assert ib.entry_baseline_replay_item(tmp_path)["status"] == "OK"
    assert ib.entry_baseline_replay_item(tmp_path / "missing") is None


def test_analyzer_stamps_health_on_the_report_row():
    src = (HERE / "analyzer_research_engine_v62.py").read_text("utf-8")
    assert 'baseline_replay["health"] = replay_health(baseline_replay)' in src
    assert '"status": baseline_replay["health"]["status"]' in src
