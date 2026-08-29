from __future__ import annotations

from research import research_dashboard as dashboard


REVISION = "8dd73bd9c485a2d4470160667c3e636c3a53365e"
EPOCH = "epoch-current"


def _install_generation(monkeypatch, *, mirror_revision=REVISION, mirror_epoch=EPOCH):
    manifest = {
        "generation_revision": REVISION,
        "generated_at": "2026-08-28T03:30:00+10:00",
        "fresh_epoch": {"epoch_id": EPOCH},
        "analyzer_sync_id": dashboard.EXPECTED_ANALYZER_SYNC_ID,
        "active_tiles": [{"policy_signature": f"tile-{index}"} for index in range(5)],
        "required_report_status": {
            name: {"available_in_generation": True, "generation_error": None}
            for name in (
                dashboard.BEST_POLICY_RESEARCH_REPORT_FILE,
                dashboard.SAFE_POLICY_GENOME_V3_REPORT_FILE,
                "qualified_exit_policy_grid_report.json",
                "exit_reports_validation.json",
            )
        },
    }
    compact = {
        "generated_at": manifest["generated_at"],
        "data_scope": "session",
        "session_scope": "SESSION",
        "performance": {},
    }
    qualified_report = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "generated_at": manifest["generated_at"],
        "epoch_id": EPOCH,
        "qualification": "QUALIFIED",
        "number_one_strategy": {"policy_id": "candidate-1"},
        "live_policy_change_allowed": True,
        "real_bitfinex_trading_allowed": True,
        "collection": {},
        "candidate_screen": {"descriptive_top_100": []},
        "safe_policy_ranking": {
            "qualification": "QUALIFIED",
            "number_one": {"policy_id": "candidate-1"},
        },
        "blockers": [],
    }

    def fake_read_json(name, default=None):
        name = str(name)
        if name.endswith(dashboard.REPORT_MANIFEST_FILE):
            return dict(manifest)
        if name.endswith(dashboard.COMPACT_SUMMARY_FILE):
            return dict(compact)
        return default or {}

    monkeypatch.setattr(dashboard, "_read_json", fake_read_json)
    monkeypatch.setattr(dashboard, "_read_report", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        dashboard, "_current_generation_report", lambda _name: dict(qualified_report)
    )
    monkeypatch.setattr(dashboard, "_manifest_reports", lambda: manifest["active_tiles"])
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: mirror_revision)
    monkeypatch.setattr(
        dashboard,
        "_mirror_sync_receipt",
        lambda: {
            "inProgress": False,
            "revisionParity": "MATCH",
            "sourceRevision": mirror_revision,
            "mirroredSourceRevision": mirror_revision,
            "observedSourceRevision": mirror_revision,
        },
    )
    monkeypatch.setattr(
        dashboard,
        "_load_bot_session",
        lambda: {
            "collector_v22_epoch_id": mirror_epoch,
            "fresh_collection_mode": True,
            "fresh_collection_start_time": 0,
            "bot_version": dashboard.EXPECTED_BOT_VERSION,
        },
    )
    monkeypatch.setattr(
        dashboard,
        "_analyzer_run_state",
        lambda: {"in_progress": False, "last_completed_at": manifest["generated_at"]},
    )
    dashboard._API_RESPONSE_CACHE.clear()


def test_revision_mismatch_is_visible_and_blocks_every_decision_surface(monkeypatch):
    _install_generation(monkeypatch, mirror_revision="different-revision")

    with dashboard.app.test_client() as client:
        health = client.get("/api/health").get_json()
        status = client.get("/api/status").get_json()
        summary = client.get("/api/summary").get_json()
        integrity = client.get("/api/integrity").get_json()
        decision = client.get("/api/decision-readiness").get_json()
        best = client.get("/api/best-policy-research").get_json()
        safe = client.get("/api/safe-policy-genome-v3.1").get_json()
        page = client.get("/").get_data(as_text=True)

    assert health["alive"] is True
    assert health["ok"] is health["ready"] is False
    assert status["ok"] is status["ready"] is False
    assert status["stale"] is True
    assert status["source_revision_parity"] == "MISMATCH"
    assert summary["stale"]["stale"] is True
    assert any("revision" in reason.lower() for reason in summary["stale"]["reasons"])
    assert integrity["valid"] is False
    assert integrity["report_status"] == "STALE_GENERATION"
    assert "Stale report" in page and "stale-banner" in page
    for payload in (decision, best, safe):
        assert payload["live_policy_change_allowed"] is False
        assert payload.get("real_bitfinex_trading_allowed", False) is False
        assert "STALE" in payload["status"]
        assert "STALE_ANALYZER_GENERATION" in payload["blockers"]
        assert payload.get("current_candidate") is None
        assert payload.get("number_one_strategy") is None


def test_exact_revision_and_epoch_match_remains_ready(monkeypatch):
    _install_generation(monkeypatch)

    with dashboard.app.test_client() as client:
        health = client.get("/api/health").get_json()
        status = client.get("/api/status").get_json()
        summary = client.get("/api/summary").get_json()
        decision = client.get("/api/decision-readiness").get_json()

    assert health["ok"] is health["ready"] is True
    assert status["ok"] is status["ready"] is True
    assert status["source_revision_parity"] == "MATCH"
    assert status["epoch_parity"] == "MATCH"
    assert status["stale"] is False
    assert summary["stale"]["stale"] is False
    assert decision["status"] == "QUALIFIED"
    assert decision["live_policy_change_allowed"] is True


def test_epoch_mismatch_blocks_even_when_revision_matches(monkeypatch):
    _install_generation(monkeypatch, mirror_epoch="epoch-different")

    with dashboard.app.test_client() as client:
        status = client.get("/api/status").get_json()
        best = client.get("/api/best-policy-research").get_json()

    assert status["source_revision_parity"] == "MATCH"
    assert status["epoch_parity"] == "MISMATCH"
    assert status["ready"] is False
    assert best["live_policy_change_allowed"] is False
    assert "EPOCH_PARITY_MISMATCH" in best["blockers"]
