"""Simplified Fly/analyzer dashboard — lean primary view, forensic behind disclosures.

bot.py cannot be imported in unit tests (exchange/WS side effects at import),
so Fly HTML/JS are extracted as string constants without importing the module.
"""
from __future__ import annotations

import ast
import re
from html.parser import HTMLParser
from pathlib import Path

from research import research_dashboard as dashboard

AGENT = Path(__file__).resolve().parent
BOT_SOURCE = (AGENT / "bot.py").read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _bot_string_const(name: str) -> str:
    for node in BOT_TREE.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing bot.py constant {name}")


class _DetailsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.owners = {}
        self.attrs = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details":
            self.stack.append(attrs.get("id"))
            self.attrs[attrs.get("id")] = attrs
        if attrs.get("id"):
            self.owners[attrs["id"]] = tuple(self.stack)

    def handle_endtag(self, tag):
        if tag == "details" and self.stack:
            self.stack.pop()


def test_analyzer_keeps_four_primary_groups_and_fences_machine_json():
    groups = dashboard.REPORT_NAV_GROUPS
    assert len(groups) == 4
    assert [gid for gid, _, _ in groups] == [
        "overview",
        "lanes-group",
        "trading-group",
        "deep-group",
    ]
    assert groups[-1][1] == "Diagnostics & Historical"
    # All historical/forensic section ids remain reachable (not deleted).
    deep_ids = {sid for sid, _, _ in groups[-1][2]}
    for sid in ("findings", "regime", "ai", "archives", "horizon", "explorer", "download"):
        assert sid in deep_ids

    parser = _DetailsParser()
    parser.feed(dashboard.DASHBOARD_HTML)
    for sid in (
        "overview-machine-json-details",
        "exit-combos-detail-inventory",
        "genome-forensic-json-details",
        "evidence-coverage-orphan-details",
        "download-bundle-list-details",
        "regime-roster-policy-details",
    ):
        assert sid in parser.attrs
        assert "open" not in parser.attrs[sid]
    assert parser.owners["exec-text"] == ("overview-machine-json-details",)
    assert parser.owners["genome-cluster"] == ("genome-forensic-json-details",)
    # Actionable overview content stays outside forensic disclosures.
    assert parser.owners["collection-status"] == ()
    assert parser.owners["strategy-leader-tiers"] == ()
    assert not parser.stack


def test_fly_dashboard_collapses_advanced_and_keeps_primary_surfaces():
    html = _bot_string_const("HTML")
    js = _bot_string_const("DASHBOARD_JS")
    parser = _DetailsParser()
    parser.feed(html)
    for sid in (
        "tradingParamsPanel",
        "supportResistancePanel",
        "exchangeCostPanel",
        "runtimeIncidentPanel",
        "debugStatePanel",
        "destructiveOpsHintPanel",
        "dataStoragePanel",
    ):
        assert sid in parser.attrs, sid
        assert "open" not in parser.attrs[sid], sid
    # Primary operator surfaces remain outside closed disclosures.
    for sid in ("operatingTruth", "pathwayLab", "marketOverview", "activityTables", "alertsSection"):
        assert parser.owners[sid] == (), sid
    assert "aiInput" in parser.owners
    assert "debugStatePanel" in parser.owners["aiInput"]
    assert "tile-card-policy-details" in js
    assert "ENTRY / EXIT / RISK — policy detail" in js
    # Balanced details markup in the static HTML shell.
    assert html.count("<details") == html.count("</details>")
    assert not parser.stack


def test_fly_dashboard_simplified_mutation_proof_open_attrs():
    """Mutation proof: if advanced panels regain open=, the lean contract fails."""
    html = _bot_string_const("HTML")
    # Intentionally break the contract in a local copy and prove the assert fires.
    broken = re.sub(
        r'<details id="tradingParamsPanel"',
        '<details id="tradingParamsPanel" open',
        html,
        count=1,
    )
    parser = _DetailsParser()
    parser.feed(broken)
    assert "open" in parser.attrs["tradingParamsPanel"]
    # Live source must still refuse open= on the advanced panels.
    live = _DetailsParser()
    live.feed(html)
    assert "open" not in live.attrs["tradingParamsPanel"]
