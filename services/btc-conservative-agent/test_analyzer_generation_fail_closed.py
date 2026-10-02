from __future__ import annotations

from research import research_dashboard as dashboard


REVISION = "8dd73bd9c485a2d4470160667c3e636c3a53365e"
EPOCH = "epoch-current"


def _install_generation(
    monkeypatch,
    *,
    mirror_revision=REVISION,
    mirror_epoch=EPOCH,
    report_source_revision=REVISION,
    observed_revision=REVISION,
):
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
    if report_source_revision is not None:
        manifest["source_revision"] = report_source_revision
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
    sync_receipt = {
        "ok": True,
        "pollOk": True,
        "inProgress": False,
        "revisionParity": "MATCH",
        "sourceRevision": mirror_revision,
        "mirroredSourceRevision": mirror_revision,
    }
    if observed_revision is not None:
        sync_receipt["observedSourceRevision"] = observed_revision
    monkeypatch.setattr(dashboard, "_mirror_sync_receipt", lambda: dict(sync_receipt))
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
    _install_generation(monkeypatch, mirror_revision="7" * 40)

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
    # The UI now distinguishes a stale saved generation from a current run;
    # retain the visible fail-closed warning and expandable exact reasons.
    assert 'id="stale-banner"' in page
    stale_branch = page.split("if (stale.stale) {", 1)[1].split("} else if", 1)[0]
    assert "banner.style.display = 'block'" in stale_branch
    assert "Stale saved analyzer generation — read-only." in stale_branch
    assert "escapeHtml(analyzerRecoveryGuidance(d))" in stale_branch
    guidance = page.split("function analyzerRecoveryGuidance", 1)[1].split("\n}", 1)[0]
    assert "Do not start a duplicate analyzer." in guidance
    assert "Show exact parity and freshness receipts" in stale_branch
    assert "escapeHtml(reasons" in stale_branch
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


def test_source_revision_requires_full_sha_or_safe_alias_with_full_anchor(monkeypatch):
    for source_revision, mirror_revision, observed_revision in (
        ("8", REVISION, REVISION),
        ("not-a-source-sha", REVISION, REVISION),
        (REVISION[:12], REVISION[:12], None),
    ):
        _install_generation(
            monkeypatch,
            report_source_revision=source_revision,
            mirror_revision=mirror_revision,
            observed_revision=observed_revision,
        )

        with dashboard.app.test_client() as client:
            status = client.get("/api/status").get_json()

        freshness = status["generation_freshness"]
        assert status["source_revision_parity"] == "UNAVAILABLE"
        assert status["ready"] is False
        assert freshness["identity_status"] == "UNVERIFIED"


def test_short_source_revision_matches_only_when_other_side_is_full_sha(monkeypatch):
    _install_generation(monkeypatch, report_source_revision=REVISION[:12])

    with dashboard.app.test_client() as client:
        status = client.get("/api/status").get_json()

    assert status["source_revision_parity"] == "MATCH"
    assert status["ready"] is True


def test_shared_alias_cannot_hide_conflicting_full_report_and_observed_shas(monkeypatch):
    conflicting_revision = REVISION[:12] + ("f" * 28)
    assert conflicting_revision != REVISION
    _install_generation(
        monkeypatch,
        report_source_revision=REVISION,
        mirror_revision=REVISION[:12],
        observed_revision=conflicting_revision,
    )

    with dashboard.app.test_client() as client:
        status = client.get("/api/status").get_json()
        summary = client.get("/api/summary").get_json()
        decision = client.get("/api/decision-readiness").get_json()

    freshness = status["generation_freshness"]
    assert status["source_revision_parity"] == "MISMATCH"
    assert status["ready"] is False
    assert freshness["source_revision_identity_status"] == "CONFLICT"
    assert freshness["source_revision_identity_conflict"] is True
    assert freshness["source_revision_identity_conflict_reason"] == "FULL_SOURCE_REVISION_CONFLICT"
    assert freshness["source_revision_full_anchor"] is None
    assert freshness["identity_status"] == "CONFLICT"
    assert any("FULL_SOURCE_REVISION_CONFLICT" in reason for reason in summary["stale"]["reasons"])
    assert decision["live_policy_change_allowed"] is False
    assert decision.get("real_bitfinex_trading_allowed", False) is False


def test_safe_aliases_resolve_only_against_one_agreed_full_sha(monkeypatch):
    for report_revision, mirror_revision, observed_revision in (
        (REVISION[:12], REVISION, REVISION),
        (REVISION, REVISION[:20], REVISION),
        (REVISION, REVISION, REVISION[:39]),
    ):
        _install_generation(
            monkeypatch,
            report_source_revision=report_revision,
            mirror_revision=mirror_revision,
            observed_revision=observed_revision,
        )

        with dashboard.app.test_client() as client:
            status = client.get("/api/status").get_json()

        freshness = status["generation_freshness"]
        assert status["source_revision_parity"] == "MATCH"
        assert freshness["observed_revision_parity"] == "MATCH"
        assert freshness["source_revision_identity_status"] == "MATCH"
        assert freshness["source_revision_identity_conflict"] is False
        assert freshness["source_revision_full_anchor"] == REVISION
        assert status["ready"] is True


def test_epoch_prefix_is_mismatch_not_current(monkeypatch):
    _install_generation(monkeypatch, mirror_epoch=EPOCH[:8])

    with dashboard.app.test_client() as client:
        status = client.get("/api/status").get_json()

    assert EPOCH.startswith(EPOCH[:8])
    assert status["epoch_parity"] == "MISMATCH"
    assert status["ready"] is False


def test_current_sync_requires_explicit_observed_fly_revision(monkeypatch):
    _install_generation(monkeypatch, observed_revision=None)

    with dashboard.app.test_client() as client:
        status = client.get("/api/status").get_json()

    freshness = status["generation_freshness"]
    assert freshness["observed_source_revision"] is None
    assert freshness["observed_revision_parity"] == "UNAVAILABLE"
    assert freshness["identity_status"] == "UNVERIFIED"
    assert status["ready"] is False


def test_report_source_identity_never_inherits_analyzer_runtime_revision(monkeypatch):
    _install_generation(monkeypatch, report_source_revision=None)

    with dashboard.app.test_client() as client:
        status = client.get("/api/status").get_json()

    freshness = status["generation_freshness"]
    assert status["generation_revision"] == REVISION
    assert freshness["analyzer_generation_revision"] == REVISION
    assert freshness["report_dataset_source_revision"] is None
    assert freshness["generation_revision"] is None
    assert status["source_revision_parity"] == "UNAVAILABLE"
    assert freshness["identity_status"] == "UNVERIFIED"
    assert status["ready"] is False


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


def test_lanes_fail_closed_while_matching_generation_sync_is_in_progress(monkeypatch):
    _install_generation(monkeypatch)
    monkeypatch.setattr(
        dashboard,
        "_mirror_sync_receipt",
        lambda: {
            "inProgress": True,
            "revisionParity": "MATCH",
            "sourceRevision": REVISION[:12],
            "mirroredSourceRevision": REVISION[:12],
            "observedSourceRevision": REVISION,
        },
    )
    monkeypatch.setattr(
        dashboard,
        "_lane_rows",
        lambda **_kwargs: (
            [{"lane": "FAMILY_ATR_TRAIL", "status": "COLLECTING"}],
            0.0,
            {"status": "CURRENT_GENERATION", "blockers": []},
        ),
    )
    dashboard._API_RESPONSE_CACHE.clear()

    with dashboard.app.test_client() as client:
        lanes = client.get("/api/lanes").get_json()
        integrity = client.get("/api/integrity").get_json()

    assert lanes["evidence_status"] == "STALE_GENERATION"
    assert lanes["evidence"]["artifact_status"] == "CURRENT_GENERATION"
    assert lanes["evidence"]["generation_freshness"]["revision_parity"] == "MATCH"
    assert lanes["evidence"]["generation_freshness"]["epoch_parity"] == "MATCH"
    assert lanes["evidence"]["generation_freshness"]["mirror_sync_in_progress"] is True
    assert any("synchronization is in progress" in item for item in lanes["evidence"]["blockers"])
    failed = integrity["failed_checks"][-1]
    assert failed["check"] == "generation_and_mirror_sync_freshness"
    assert failed["found"]["revision_parity"] == "MATCH"
    assert failed["found"]["mirror_sync_in_progress"] is True
    dashboard._API_RESPONSE_CACHE.clear()


def test_lanes_remain_current_for_matching_short_and_full_revision(monkeypatch):
    _install_generation(monkeypatch, mirror_revision=REVISION[:12])
    monkeypatch.setattr(
        dashboard,
        "_lane_rows",
        lambda **_kwargs: (
            [{"lane": "FAMILY_ATR_TRAIL", "status": "COLLECTING"}],
            0.0,
            {"status": "CURRENT_GENERATION", "blockers": []},
        ),
    )
    dashboard._API_RESPONSE_CACHE.clear()

    with dashboard.app.test_client() as client:
        lanes = client.get("/api/lanes").get_json()

    assert lanes["evidence_status"] == "CURRENT_GENERATION"
    dashboard._API_RESPONSE_CACHE.clear()


def test_lanes_preserve_unavailable_artifact_status_when_generation_is_stale(monkeypatch):
    _install_generation(monkeypatch, mirror_revision="7" * 40)
    unavailable = {
        "status": "UNAVAILABLE_CURRENT_GENERATION",
        "blockers": ["CURRENT_REPORT_MISSING"],
    }
    monkeypatch.setattr(
        dashboard,
        "_lane_rows",
        lambda **_kwargs: ([], 0.0, dict(unavailable)),
    )
    dashboard._API_RESPONSE_CACHE.clear()

    with dashboard.app.test_client() as client:
        lanes = client.get("/api/lanes").get_json()

    assert lanes["evidence_status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert lanes["evidence"] == unavailable
    dashboard._API_RESPONSE_CACHE.clear()


def test_lane_ui_surfaces_stale_reason_and_neutralizes_performance_status(monkeypatch):
    _install_generation(monkeypatch)

    with dashboard.app.test_client() as client:
        page = client.get("/").get_data(as_text=True)

    assert "Evidence status: STALE ANALYZER GENERATION" in page
    assert "...(evidence.blockers || [])" in page
    assert "'STALE / UNAVAILABLE'" in page
