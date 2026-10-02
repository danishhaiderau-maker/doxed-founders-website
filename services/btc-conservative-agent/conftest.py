import os

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


@pytest.fixture(autouse=True)
def _no_runtime_state_written_to_source_dir():
    paths = [os.path.join(_SOURCE_DIR, name) for name in _RUNTIME_STATE_IN_SOURCE]
    preexisting = {path for path in paths if os.path.exists(path)}
    yield
    leaked = [path for path in paths if path not in preexisting and os.path.exists(path)]
    for path in leaked:
        os.remove(path)
    assert not leaked, f"test wrote runtime state into the source folder: {leaked}"