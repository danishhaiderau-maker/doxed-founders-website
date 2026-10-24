"""RK D (24 Oct): an analyzer stuck in RUNNING for more than 2 h trips the staleness alert, on BOTH
analyzer.generation and analyzer.cycle, whichever status file shows the stuck run."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import system_health as sh  # noqa: E402
from test_system_health import by_id, healthy, ts  # noqa: E402

NOW = ts("2026-10-24T09:00:00Z")
STUCK = 2 * 3600 + 60


def _stuck_inputs(*, cycle_open=True, cycle_started=True):
    inputs = healthy(NOW)
    inputs["analyzer_status"] = {"state": "RUNNING", "exitCode": None, "startedAt": sh.iso(NOW - STUCK),
                                 "lastCompletedGenerationAt": sh.iso(NOW - STUCK - 600),
                                 "lastSuccessAt": sh.iso(NOW - STUCK - 600)}
    if cycle_open:
        inputs["cycle_status"] = {"startedAt": sh.iso(NOW - STUCK - 60) if cycle_started else None,
                                  "finishedAt": None, "phase": "ANALYZER"}
    else:  # the cycle status still shows the previous, finished cycle
        inputs["cycle_status"] = {"startedAt": sh.iso(NOW - STUCK - 1500), "finishedAt": sh.iso(NOW - STUCK - 600),
                                  "exitCode": 0}
    return inputs


@pytest.mark.parametrize("cycle_open,cycle_started", [(True, True), (False, True), (True, False)])
def test_stuck_running_over_2h_trips_generation_and_cycle(cycle_open, cycle_started):
    checks = by_id(sh.evaluate(_stuck_inputs(cycle_open=cycle_open, cycle_started=cycle_started), {}))
    assert checks["analyzer.generation"]["status"] == sh.RED
    assert checks["analyzer.cycle"]["status"] == sh.RED, checks["analyzer.cycle"]["observed"]


def test_running_with_fresh_success_stamp_still_trips_generation():
    inputs = _stuck_inputs()
    inputs["analyzer_status"]["lastSuccessAt"] = sh.iso(NOW - 60)  # a non-generation success stamp
    inputs["analyzer_status"].pop("lastCompletedGenerationAt")
    checks = by_id(sh.evaluate(inputs, {}))
    assert checks["analyzer.generation"]["status"] == sh.RED


def test_short_run_is_not_stuck():
    inputs = healthy(NOW)
    inputs["analyzer_status"].update(state="RUNNING", startedAt=sh.iso(NOW - 600))
    inputs["cycle_status"] = {"startedAt": sh.iso(NOW - 700), "finishedAt": None, "phase": "ANALYZER"}
    checks = by_id(sh.evaluate(inputs, {}))
    assert checks["analyzer.generation"]["status"] == sh.GREEN
    assert checks["analyzer.cycle"]["status"] == sh.GREEN
