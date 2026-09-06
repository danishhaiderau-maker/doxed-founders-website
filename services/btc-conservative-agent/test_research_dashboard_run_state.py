from pathlib import Path

import pytest

from research import research_dashboard as dashboard


def run_state(tmp_path, monkeypatch, body):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "ANALYZER_LOG_FILE", "run.log")
    (tmp_path / "run.log").write_text(
        f"# analyzer run 2026-09-06T00:00:00Z | sync={dashboard.EXPECTED_ANALYZER_SYNC_ID}\n" + body,
        encoding="utf-8",
    )
    return dashboard._analyzer_run_state()


@pytest.mark.parametrize("body", [
    "ANALYZER ITERATION 1 FAILED: CANONICAL_DATASET_MANIFEST_MISSING\nIteration 1 complete (recovered from error)\n",
    "Iteration 1 complete (recovered from error)\n",
    "ANALYZER ITERATION 1 FAILED: CANONICAL_DATASET_MANIFEST_MISSING\n",
    "Iteration 1 complete\nANALYZER v62 ITERATION 2 START ===\nANALYZER ITERATION 2 FAILED: missing\nIteration 2 complete (recovered from error)\n",
])
def test_failed_iteration_is_not_success_or_running(tmp_path, monkeypatch, body):
    result = run_state(tmp_path, monkeypatch, body)
    assert result["phase"] == "FAILED"
    assert result["in_progress"] is False
    assert result["last_completed_at"] is None


@pytest.mark.parametrize("suffix,phase", [
    ("ANALYZER v62 ITERATION 2 START ===\n", "RUNNING"),
    ("ANALYZER v62 ITERATION 2 START ===\nIteration 2 complete\n", "IDLE_BETWEEN_RUNS"),
])
def test_old_failure_does_not_poison_new_iteration(tmp_path, monkeypatch, suffix, phase):
    result = run_state(tmp_path, monkeypatch,
        "ANALYZER ITERATION 1 FAILED: missing\nIteration 1 complete (recovered from error)\n" + suffix)
    assert result["phase"] == phase
    assert (result["last_completed_at"] is not None) == (phase == "IDLE_BETWEEN_RUNS")


def test_large_log_does_not_use_unbounded_read_bytes(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("whole file read forbidden")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    result = run_state(tmp_path, monkeypatch, "x" * 100000 + "\nIteration 4 complete (recovered from error)\n")
    assert result["phase"] == "FAILED"
