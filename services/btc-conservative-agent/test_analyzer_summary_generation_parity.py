"""Regression coverage for current-generation analyzer summary parity."""

from research import research_dashboard as dashboard


CURRENT_REV = "ef52d8d01f7a4b075ef73843e483618acb08e1fc"
CURRENT_EPOCH = "epoch-ca59fec3c223953a05bc0da4"


def _summary_client(monkeypatch, tmp_path, *, compact, direct_edge, manifest):
    payloads = {
        str(dashboard.COMPACT_SUMMARY_FILE): compact,
        str(dashboard.REPORT_MANIFEST_FILE): manifest,
        "real_edge_summary.json": direct_edge,
        str(dashboard.HISTORICAL_COHORT_REPORT_FILE): {
            "scope": "HISTORICAL_EXECUTED_DEDUP",
            "unique_trades": 25,
        },
        str(dashboard.RETENTION_STATUS_FILE): {},
    }
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        dashboard,
        "_read_json",
        lambda name, default=None: payloads.get(str(name), default if default is not None else {}),
    )
    monkeypatch.setattr(dashboard, "_summary_stale_meta", lambda compact: {"stale": False, "reasons": []})
    monkeypatch.setattr(dashboard, "_integrity_payload", lambda: {"valid": True, "report_status": "VALID"})
    monkeypatch.setattr(dashboard, "_read_text", lambda name: "")
    dashboard._API_RESPONSE_CACHE.clear()
    return dashboard.app.test_client()


def test_summary_excludes_stale_real_edge_instead_of_turning_zero_into_44(tmp_path, monkeypatch):
    manifest = {
        "generated_at": "2026-08-26T21:10:45+00:00",
        "generation_revision": CURRENT_REV,
        "fresh_epoch": {"epoch_id": CURRENT_EPOCH},
        "performance": {"trades": 0, "net_pnl_usd": None, "expectancy_usd": None},
    }
    stale_edge = {
        "generation_revision": "262816dfb8c159d244861fb9bc6e336837293d0d",
        "analysis_provenance": {"fresh_epoch_id": "epoch-f2ea95e53b3ff599a9419514"},
        "executed": 44,
        "executed_pnl_usd": -2.39,
        "approve_attempts": 297,
    }
    client = _summary_client(
        monkeypatch,
        tmp_path,
        compact={"performance": {"trades": 0}, "real_edge": stale_edge},
        direct_edge=stale_edge,
        manifest=manifest,
    )

    payload = client.get("/api/summary").get_json()

    assert payload["performance"] == manifest["performance"]
    assert payload["performance_source"] == "CURRENT_ATOMIC_MANIFEST"
    assert payload["generated_at"] == manifest["generated_at"]
    assert payload["approve_to_fill_pct"] is None
    assert payload["real_edge"]["status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert "executed" not in payload["real_edge"]
    assert len(payload["real_edge"]["excluded_candidates"]) == 2
    assert {row["reason"] for row in payload["real_edge"]["excluded_candidates"]} == {
        "CROSS_GENERATION_IDENTITY_MISMATCH"
    }
    # Historical evidence remains explicitly separate rather than becoming a
    # current KPI.
    assert payload["historical_cohort"]["unique_trades"] == 25
    dashboard._API_RESPONSE_CACHE.clear()


def test_atomic_manifest_count_remains_authoritative_even_for_matching_edge(tmp_path, monkeypatch):
    manifest = {
        "generated_at": "2026-08-26T21:40:45+00:00",
        "generation_revision": CURRENT_REV,
        "fresh_epoch": {"epoch_id": CURRENT_EPOCH},
        "performance": {"trades": 0, "net_pnl_usd": None},
    }
    matching_edge = {
        "generation_revision": CURRENT_REV,
        "analysis_provenance": {"fresh_epoch_id": CURRENT_EPOCH},
        "executed": 44,
        "executed_pnl_usd": -2.39,
        "approve_attempts": 100,
    }
    client = _summary_client(
        monkeypatch,
        tmp_path,
        compact={"performance": {"trades": 0}, "real_edge": matching_edge},
        direct_edge={},
        manifest=manifest,
    )

    payload = client.get("/api/summary").get_json()

    assert payload["real_edge"]["executed"] == 44
    assert payload["performance"]["trades"] == 0
    assert payload["performance"]["net_pnl_usd"] is None
    assert payload["performance_source"] == "CURRENT_ATOMIC_MANIFEST"
    assert "['Fresh executed', p.trades ?? 0]" in dashboard.DASHBOARD_HTML
    dashboard._API_RESPONSE_CACHE.clear()
