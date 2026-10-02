import os

# research_dashboard validates these at import time and only accepts this
# checkout's canonical-research-data store; values inherited from an analyzer
# or developer shell (v2c, legacy mirror) must not decide test outcomes.
# Tests that need them set them explicitly.
for _name in ("BTC_AGENT_DATA_DIR", "BTC_AGENT_REPORT_DIR"):
    os.environ.pop(_name, None)
