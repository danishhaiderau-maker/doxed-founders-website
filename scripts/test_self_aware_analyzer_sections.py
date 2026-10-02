"""Self-aware 2-hourly check of the :9001 analyzer sections (populated, fresh, dimension-complete, consistent)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import analyzer_sections as an  # noqa: E402
from self_aware import diagnose  # noqa: E402
from self_aware.config import CADENCE_SEC, THRESHOLDS, Paths  # noqa: E402
from self_aware.facts import iso  # noqa: E402

NOW = 1_790_930_000.0


def _paths(tmp: Path) -> Paths:
    return Paths(home=tmp / "home", chain=tmp / "chain", mirror=tmp / "mirror", mirror_archive=tmp / "am",
                 puller=tmp / "pu", exports=tmp / "ex", archive=tmp / "ar", diagnostics=tmp / "d",
                 analyzer_repo=tmp / "v2c", retention=tmp / "ret")


def _section(sid, severity="GREEN", rows=50, checks=(), core=True):
    return {"id": sid, "label": sid, "kind": "details_section", "core": core, "severity": severity, "rows": rows,
            "newest_generated_at": iso(NOW), "apis": [f"/api/{sid}"], "checks": list(checks)}


def _health(*sections):
    return {"verdict": "GREEN", "counts": {}, "generated_at": iso(NOW), "sections": list(sections)}


def _setup(tmp, monkeypatch, grid_age=600, opportunities=1000, evaluated=900, parity="MATCH"):
    report = tmp / "genome_grid_report.json"
    report.write_text(json.dumps({"generated_at": iso(NOW - grid_age), "coverage": {"episodes_evaluated": evaluated},
                                  "grid": {"policies_evaluated": 20000, "policies_ranked": 30},
                                  "canonical_parity": {"status": parity}}), encoding="utf-8")
    monkeypatch.setattr(an, "GENOME_GRID_REPORT", report)
    led = tmp / "mirror" / "v3" / "ledgers"
    led.mkdir(parents=True)
    (led / "opportunity.jsonl").write_text("{}\n" * opportunities, encoding="utf-8")


def _sev(doc):
    return {f["id"]: f["severity"] for f in an.findings(doc, NOW, THRESHOLDS["sections_doc_max_age_sec"])}


def test_cadence_is_two_hours():
    assert CADENCE_SEC["sections"] == 7200


def test_green_when_sections_populated_and_grid_fresh(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    dims = [{"id": "genome_axes_complete", "severity": "GREEN", "observed": "18 axes"}]
    body = _health(_section("summary"), _section("combos", checks=dims), _section("genome"))
    doc = an.run(_paths(tmp_path), {}, NOW, fetch=lambda url, timeout: (body, None, 1.0))
    assert _sev(doc) == {"analyzer.sections": "GREEN", "analyzer.dimensions": "GREEN", "analyzer.consistency": "GREEN"}
    assert doc["collected_opportunities"] == 1000


def test_red_when_dashboard_unreachable_or_top100_collapsed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    doc = an.run(_paths(tmp_path), {}, NOW, fetch=lambda url, timeout: (None, "timed out", 240.0))
    assert _sev(doc)["analyzer.sections"] == "RED"
    collapsed = [{"id": "genome_grid_present", "severity": "RED", "observed": "only legacy dims"}]
    body = _health(_section("combos", severity="RED", checks=collapsed))
    doc = an.run(_paths(tmp_path), {}, NOW, fetch=lambda url, timeout: (body, None, 1.0))
    assert _sev(doc)["analyzer.dimensions"] == "RED"


def test_stale_core_section_and_stale_grid(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, grid_age=13 * 3600, evaluated=100, parity="MISMATCH")
    stale = [{"id": "fresh", "severity": "AMBER", "observed": "newest generated_at 4.0 h old"}]
    body = _health(_section("summary", severity="AMBER", checks=stale))
    doc = an.run(_paths(tmp_path), {}, NOW, fetch=lambda url, timeout: (body, None, 1.0))
    sev = _sev(doc)
    assert sev["analyzer.sections"] == "AMBER" and sev["analyzer.consistency"] == "RED"


def test_shrink_between_runs_is_amber(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    state: dict = {}
    an.run(_paths(tmp_path), state, NOW, fetch=lambda u, t: (_health(_section("combos", rows=200)), None, 1.0))
    doc = an.run(_paths(tmp_path), state, NOW, fetch=lambda u, t: (_health(_section("combos", rows=5)), None, 1.0))
    assert doc["shrank"] == [{"id": "combos", "rows_before": 200, "rows_now": 5}]
    assert _sev(doc)["analyzer.consistency"] == "AMBER"


def test_skip_when_not_run_recently_and_diagnose_wiring(tmp_path, monkeypatch):
    assert set(_sev(None).values()) == {"SKIP"}
    _setup(tmp_path, monkeypatch)
    doc = an.run(_paths(tmp_path), {}, NOW, fetch=lambda u, t: (_health(_section("summary")), None, 1.0))
    found = diagnose.check_analyzer_sections({"analyzer_sections": doc, "now": NOW}, {}, None)
    assert [f.id for f in found] == ["analyzer.sections", "analyzer.dimensions", "analyzer.consistency"]
    assert all(f.category == "analyzer" for f in found)
    old = dict(doc, generated_at=iso(NOW - 4 * 3600))
    assert {f.severity for f in diagnose.check_analyzer_sections({"analyzer_sections": old, "now": NOW}, {}, None)} == {"SKIP"}