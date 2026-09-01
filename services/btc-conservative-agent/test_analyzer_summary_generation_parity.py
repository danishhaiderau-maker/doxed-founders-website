"""Regression coverage for current-generation analyzer summary parity."""

from research import research_dashboard as dashboard


CURRENT_REV = "ef52d8d01f7a4b075ef73843e483618acb08e1fc"
CURRENT_EPOCH = "epoch-ca59fec3c223953a05bc0da4"


def _summary_client(
    monkeypatch, tmp_path, *, compact, direct_edge, manifest,
    lifecycle_report=None,
):
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
    monkeypatch.setattr(
        dashboard,
        "_read_report",
        lambda name, default=None: (
            lifecycle_report
            if name == dashboard.LIFECYCLE_BUNDLE_INVENTORY_REPORT_FILE
            and isinstance(lifecycle_report, dict)
            else (default if default is not None else {})
        ),
    )
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


def test_summary_exposes_current_lifecycle_bundle_counts_as_audit_evidence(
    tmp_path, monkeypatch,
):
    manifest = {
        "generated_at": "2026-09-01T03:00:00+00:00",
        "generation_revision": CURRENT_REV,
        "fresh_epoch": {"epoch_id": CURRENT_EPOCH},
        "performance": {"trades": 0, "net_pnl_usd": None},
    }
    inventory = {
        "schema": "lifecycle_bundle_inventory_v1",
        "inventory_scope": "MANIFEST_ONLY",
        "complete": True,
        "complete_scope": "MANIFEST_INVENTORY",
        "payload_verification_status": "UNKNOWN_NOT_SCANNED",
        "payload_files_read": 0,
        "scan": {"truncated": False},
        "analysis_provenance": {
            "generation_revision": CURRENT_REV,
            "fresh_epoch_id": CURRENT_EPOCH,
        },
        "qualification": {"unique_lifecycle_count": 3},
        "transfer": {
            "unique_lifecycle_count": 5,
            "audit_only": True,
            "ranking_eligible": False,
            "profitability_supported": False,
            "source_cleanup_authorized": False,
        },
        "parity": {
            "intersection_count": 3,
            "qualification_only_count": 0,
            "transfer_only_count": 2,
            "complete": True,
        },
        "invalid_manifest_count": 1,
    }
    client = _summary_client(
        monkeypatch, tmp_path, compact={"performance": {}}, direct_edge={},
        manifest=manifest, lifecycle_report=inventory,
    )

    payload = client.get("/api/summary").get_json()["lifecycle_bundles"]

    assert payload["status"] == "AVAILABLE_CURRENT_GENERATION"
    assert payload["qualification_count"] == 3
    assert payload["transfer_audit_count"] == 5
    assert payload["invalid_count"] == 1
    assert payload["parity"]["intersection_count"] == 3
    assert payload["qualification_label"] == "manifest-verified qualification bundles"
    assert payload["payload_verification_status"] == "UNKNOWN_NOT_SCANNED"
    assert payload["transfer_label"] == "transfer-ready audit copies"
    assert payload["transfer_audit_only"] is True
    assert payload["transfer_ranking_eligible"] is False
    assert payload["transfer_profitability_supported"] is False
    assert payload["transfer_source_cleanup_authorized"] is False
    assert not any(
        word in payload for word in ("trades", "fills", "winners", "pnl", "ev")
    )
    dashboard._API_RESPONSE_CACHE.clear()

    inventory["complete"] = False
    inventory["scan"] = {"truncated": True, "blocker_counts": {"RUNTIME_LIMIT_EXCEEDED": 1}}
    truncated_client = _summary_client(
        monkeypatch, tmp_path, compact={"performance": {}}, direct_edge={},
        manifest=manifest, lifecycle_report=inventory,
    )
    truncated = truncated_client.get("/api/summary").get_json()["lifecycle_bundles"]
    assert truncated["status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert truncated["qualification_count"] is None
    assert truncated["transfer_audit_count"] is None
    dashboard._API_RESPONSE_CACHE.clear()


def test_summary_rejects_stale_or_undeclared_lifecycle_inventory(
    tmp_path, monkeypatch,
):
    manifest = {
        "generated_at": "2026-09-01T03:00:00+00:00",
        "generation_revision": CURRENT_REV,
        "fresh_epoch": {"epoch_id": CURRENT_EPOCH},
        "performance": {"trades": 0},
    }
    stale = {
        "schema": "lifecycle_bundle_inventory_v1",
        "analysis_provenance": {
            "generation_revision": "b" * 40,
            "fresh_epoch_id": "epoch-stale",
        },
        "qualification": {"unique_lifecycle_count": 99},
        "transfer": {
            "unique_lifecycle_count": 99,
            "audit_only": True,
            "ranking_eligible": False,
            "profitability_supported": False,
            "source_cleanup_authorized": False,
        },
        "invalid_manifest_count": 0,
    }
    stale_client = _summary_client(
        monkeypatch, tmp_path, compact={"performance": {}}, direct_edge={},
        manifest=manifest, lifecycle_report=stale,
    )
    stale_payload = stale_client.get("/api/summary").get_json()["lifecycle_bundles"]
    assert stale_payload["status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert stale_payload["qualification_count"] is None
    assert stale_payload["transfer_audit_count"] is None
    dashboard._API_RESPONSE_CACHE.clear()

    undeclared_client = _summary_client(
        monkeypatch, tmp_path, compact={"performance": {}}, direct_edge={},
        manifest=manifest, lifecycle_report=None,
    )
    undeclared = undeclared_client.get("/api/summary").get_json()["lifecycle_bundles"]
    assert undeclared["status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert undeclared["reason"] == "REPORT_NOT_IN_CURRENT_GENERATION"
    assert undeclared["transfer_ranking_eligible"] is False
    assert undeclared["transfer_profitability_supported"] is False
    assert undeclared["transfer_source_cleanup_authorized"] is False
    dashboard._API_RESPONSE_CACHE.clear()


def test_lifecycle_bundle_html_uses_non_trading_audit_labels():
    html = dashboard.DASHBOARD_HTML
    assert 'id="lifecycle-bundle-kpis"' in html
    assert "manifest-verified qualification bundles" in html
    assert "transfer-ready audit copies" in html
    assert "ranking eligible=false" in html
    assert "profitability supported=false" in html
    assert "source cleanup authorized=false" in html
    summary_body = html[html.index("async function loadSummary()"):
                        html.index("async function loadDecisionReadiness()")]
    assert "const lifecycleBundles = d.lifecycle_bundles || {};" in summary_body
    assert "const bundleKpis = document.getElementById('lifecycle-bundle-kpis');" in summary_body
