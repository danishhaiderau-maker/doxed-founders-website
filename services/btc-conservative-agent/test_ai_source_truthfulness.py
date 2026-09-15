from pathlib import Path
import ast


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_cassette_replay_cannot_be_labeled_fresh():
    """Demo/cassette data must remain visibly synthetic in AI evidence."""
    assert 'ai_response_source = "CASSETTE_REPLAY"' in SOURCE
    assert 'ai_response_synthetic = True' in SOURCE
    assert '"source": ai_response_source' in SOURCE
    assert '"synthetic_response": ai_response_synthetic' in SOURCE


def test_real_provider_path_keeps_fresh_source():
    """The non-cassette provider path remains the only FRESH source."""
    assert 'ai_response_source = "FRESH"' in SOURCE
    assert 'log_pipeline_event("AI", "API_OK", "DEEPSEEK_RESPONSE"' in SOURCE


def test_standalone_ai_input_row_carries_provider_provenance():
    """AI input exports must be self-describing without a history join."""
    assert '"source": ai_result.get("source")' in SOURCE
    assert '"synthetic_response": bool(ai_result.get("synthetic_response", False))' in SOURCE


def test_dashboard_sync_cannot_forge_provider_completion_receipt():
    """Only the provider result path may advance the watchdog completion clock."""
    tree = ast.parse(SOURCE)
    sync = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_sync_ai_dashboard_debug"
    )
    sync_source = ast.get_source_segment(SOURCE, sync) or ""
    assert "last_ai_provider_result_ts" not in sync_source
    assert "def _record_ai_provider_result_receipt" in SOURCE
    assert 'state["last_ai_provider_result_ts"] = time.time()' in SOURCE
    assert 'state["last_ai_provider_result_error"] = bool(result.get("ai_error", False))' in SOURCE


def test_paper_resume_defaults_on_without_erasing_current_session_pause():
    """A prior paper pause expires at boot, but an active session pause remains."""
    assert 'os.getenv("PAPER_OPERATOR_PAUSE_SCOPE", "SESSION")' in SOURCE
    assert "manual_pause_already_active" in SOURCE
    assert '"CLEARED_PREVIOUS_SESSION_OPERATOR_PAUSE"' in SOURCE
    assert '"PERSISTED_OUTSIDE_PAPER_SESSION"' in SOURCE


def test_status_exposes_pause_boot_and_provider_receipts():
    """Operators can distinguish a real provider call from a UI-only refresh."""
    assert '"operator_pause_boot_receipt": copy.deepcopy(state.get("operator_pause_boot_receipt"))' in SOURCE
    assert '"ai_provider_receipt": {' in SOURCE
    assert '"completed_ts": float(state.get("last_ai_provider_result_ts") or 0)' in SOURCE


def test_scheduled_observation_keeps_real_ai_cadence_when_entry_only_gate_fails():
    """Manual/entry-only pauses may suppress orders, not provider evidence."""
    assert "def can_run_scheduled_research_observation" in SOURCE
    assert '"SCHEDULED_RESEARCH_OBSERVATION_ONLY"' in SOURCE
    assert '"scheduled_research_observation_only"' in SOURCE
    assert "runtime.get(\"structural_prerequisites_ready\")" in SOURCE
    assert "shared_research_ai_observation_enabled()" in SOURCE


def test_scheduled_observation_is_order_suppressed_and_provenance_labeled():
    """Scheduled observations must enter the shadow lane and carry its reason."""
    assert 'event["paused_shadow_mode"] = True' in SOURCE
    assert 'event["research_observation_only"] = True' in SOURCE
    assert '"scheduled_research_observation_only": scheduled_observation_only' in SOURCE
    assert 'event.get("paused_shadow_reason")' in SOURCE


def test_scheduled_observation_bypasses_quality_skip_but_not_cooldown():
    """A real scheduled slot must not disappear behind a secondary quality gate."""
    assert 'if ctx.get("scheduled_research_assessment") or ctx.get("scheduled_research_observation_only"):' in SOURCE
    assert 'return True, "SCHEDULED_RESEARCH_AI"' in SOURCE
    assert 'if ai_cooldown_remaining_sec() > 0:' in SOURCE
    assert 'and (not execution_paused or entry_only_pause)' in SOURCE
    assert 'or manual_pause or entry_only_pause' not in SOURCE


def test_scheduled_assessment_is_separate_from_no_order_observation_mode():
    """Resumed cadence keeps assessing; only entry blocks suppress orders."""
    assert 'scheduled_research_assessment = bool(_sole_ai_research_mode())' in SOURCE
    assert 'event["scheduled_research_assessment"] = True' in SOURCE
    assert 'ctx["scheduled_research_assessment"] = True' in SOURCE
