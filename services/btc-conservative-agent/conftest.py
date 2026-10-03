import os
import shutil

import pytest

# research_dashboard validates these at import time and only accepts this
# checkout's canonical-research-data store; values inherited from an analyzer
# or developer shell (v2c, legacy mirror) must not decide test outcomes.
# Tests that need them set them explicitly.
for _name in ("BTC_AGENT_DATA_DIR", "BTC_AGENT_REPORT_DIR"):
    os.environ.pop(_name, None)

_SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
# cwd-relative runtime state that cwd-fallback readers (bot, accumulator,
# analyzer) would pick up from the source folder on the next run.
_RUNTIME_STATE_IN_SOURCE = ("research_session.json", "v3")
_SESSION_PREEXISTING = set()


def _runtime_state_paths():
    return [os.path.join(_SOURCE_DIR, name) for name in _RUNTIME_STATE_IN_SOURCE]


def _remove_runtime_state(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        os.remove(path)


def pytest_sessionstart(session):
    _SESSION_PREEXISTING.update(path for path in _runtime_state_paths() if os.path.exists(path))


def pytest_sessionfinish(session, exitstatus):
    # Module-level test code runs at collection, outside the per-test fixture.
    leaked = [path for path in _runtime_state_paths()
              if path not in _SESSION_PREEXISTING and os.path.exists(path)]
    for path in leaked:
        _remove_runtime_state(path)
    if leaked:
        print(f"\nERROR: test session wrote runtime state into the source folder: {leaked}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _no_runtime_state_written_to_source_dir():
    paths = _runtime_state_paths()
    preexisting = {path for path in paths if os.path.exists(path)}
    yield
    leaked = [path for path in paths if path not in preexisting and os.path.exists(path)]
    for path in leaked:
        _remove_runtime_state(path)
    assert not leaked, f"test wrote runtime state into the source folder: {leaked}"
