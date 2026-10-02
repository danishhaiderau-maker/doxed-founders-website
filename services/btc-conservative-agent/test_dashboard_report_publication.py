"""Every report a :9001 tab fetches by name must be part of the atomic publication.

Once an atomic generation exists, /api/report/<name> serves only files the
published manifest declares, and the manifest declares only
DEEP_DIVE_REPORT_CATALOG entries. A tab fetching anything else is a dead path
(the Regime tab 404'd this way although the engine wrote the file every pass).
"""

import re
from pathlib import Path

import analyzer_research_engine_v62 as engine

DASHBOARD = Path(__file__).resolve().parent / "research" / "research_dashboard.py"


def test_every_dashboard_report_fetch_is_published():
    fetched = set(re.findall(r"/api/report/([A-Za-z0-9_.\-]+\.json)", DASHBOARD.read_text(encoding="utf-8")))
    published = {fname for _, fname, _ in engine.DEEP_DIVE_REPORT_CATALOG}
    assert fetched, "dashboard no longer fetches reports by name; update this contract"
    assert sorted(fetched - published) == []


def test_regime_tab_sources_are_published():
    published = {fname for _, fname, _ in engine.DEEP_DIVE_REPORT_CATALOG}
    assert engine.REGIME_LEADERBOARD_REPORT_FILE in published
    assert engine.ROSTER_POLICY_FILE in published
