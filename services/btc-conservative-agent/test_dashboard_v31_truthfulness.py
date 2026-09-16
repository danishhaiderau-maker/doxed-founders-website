"""Focused dashboard regressions for signed V3.1 evidence identity and freshness."""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _function_source(name: str) -> str:
    source = ast.get_source_segment(BOT_SOURCE, _function(name))
    assert source is not None
    return source


def _isolated_function(name: str, namespace: dict):
    function = _function(name)
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace[name]


def test_active_overlay_refreshes_ai_history_counters_and_server_clock() -> None:
    body = _function_source("_api_state_cache_refresher_loop")
    for assignment in (
        'snap["ai_history"]',
        'snap["ai_history_total"]',
        'snap["ai_call_count"]',
        'snap["stability_ai_call_count"]',
        'snap["last_ai_call_ts"]',
        'snap["lane_last_ai_call_ts"]',
        'snap["ai_input"]',
        'snap["ai_input_time"]',
        'snap["ai_input_time_melbourne"]',
        'snap["server_ts_melbourne"]',
    ):
        assert assignment in body, f"active overlay no longer refreshes {assignment}"
    assert '"server_ts"' in body
    assert "_format_melbourne_hm(" in body
    assert 'snap.get("server_ts") or utc_iso()' in body


def test_api_exposes_collector_and_legacy_writer_as_separate_identities() -> None:
    snapshot = _function_source("_build_api_state_snapshot")
    overlay = _function_source("_api_state_cache_refresher_loop")
    for body in (snapshot, overlay):
        assert '"collector_version"' in body
        assert '"legacy_collector_version"' in body
    assert 'safeText(\'collectorVersionBanner\', d.collector_version || \'UNKNOWN\')' in BOT_SOURCE
    assert 'safeText(\'legacyCollectorVersionBanner\', d.legacy_collector_version || \'none\')' in BOT_SOURCE


def test_dashboard_projects_collection_receipts_without_claiming_a_rank() -> None:
    snapshot = _function_source("_build_api_state_snapshot")
    overlay = _function_source("_api_state_cache_refresher_loop")
    projection = _function_source("_research_collection_dashboard_projection")

    assert 'snapshot["research_collection"] = _research_collection_dashboard_projection(now_ts)' in snapshot
    assert 'snap["research_collection"] = _research_collection_dashboard_projection()' in overlay
    for field in (
        '"order_multiverse_pending"',
        '"order_multiverse_written"',
        '"rows_written_this_process"',
        '"skipped_buckets_this_process"',
        '"write_failures_this_process"',
        '"candidate_count"',
        '"completion_bundles"',
        '"transfer_bundles"',
        '"UNKNOWN_NOT_SCANNED"',
        '"UNSCOPED_RUNTIME_TELEMETRY"',
        '"ack_count"',
        '"blocker_counts"',
    ):
        assert field in projection
    assert '"CURRENT_ACK_UNVERIFIED"' in projection
    assert '"ACK_PRESENT_ANALYZER_PUBLICATION_REQUIRED"' in projection
    assert '_current_generation_ack_is_current(' in projection
    projection_calls = {
        node.func.id
        for node in ast.walk(_function("_research_collection_dashboard_projection"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_lifecycle_pipeline_public_status" not in projection_calls
    assert "_lifecycle_artifact_counts" not in projection_calls
    assert "renderResearchCollection(d.research_collection);" in BOT_SOURCE
    assert 'id="researchCollectionPanel"' in BOT_SOURCE
    assert '"research_collection",' in BOT_SOURCE


def test_current_generation_ack_truth_table_requires_identity_and_freshness() -> None:
    fn = _isolated_function("_current_generation_ack_is_current", {"math": math, "re": re})
    now = 1_000.0
    generation_id = "canonical-generation-20260917"
    generation_sha = "a" * 64
    current_generation = {
        "generation_id": generation_id,
        "generation_sha256": generation_sha,
        "authoritative_generation": True,
    }
    valid_ack = {
        "count": 1,
        "generation_id": generation_id,
        "generation_sha256": generation_sha,
        "canonical_generation": True,
        "current_generation": True,
        "freshness_status": "FRESH",
        "newest_age_sec": 3.0,
        "freshness_max_age_sec": 30.0,
        "observed_at": 997.0,
    }
    cases = {
        "absent": (None, False),
        "malformed": ({"count": "1"}, False),
        "zero": ({**valid_ack, "count": 0}, False),
        "stale": ({**valid_ack, "newest_age_sec": 31.0}, False),
        "frozen_stale_age": ({**valid_ack, "observed_at": 900.0}, False),
        "mismatched": ({**valid_ack, "generation_sha256": "b" * 64}, False),
        "missing_freshness": ({key: value for key, value in valid_ack.items()
                               if key not in {"freshness_status", "newest_age_sec", "freshness_max_age_sec"}}, False),
        "missing_timestamp": ({key: value for key, value in valid_ack.items()
                              if key != "observed_at"}, False),
        "infinite_age": ({**valid_ack, "newest_age_sec": math.inf}, False),
        "infinite_max": ({**valid_ack, "freshness_max_age_sec": math.inf}, False),
        "valid": (valid_ack, True),
    }
    for name, (ack, expected) in cases.items():
        assert fn(ack, current_generation, now) is expected, name


def test_health_separates_diagnostic_and_qualification_fill_worlds() -> None:
    body = _function_source("status")
    assert '"fill_model": "SEPARATED_EVIDENCE_WORLDS"' in body
    assert '"diagnostic_fill_model": "IDEAL_TOUCH_DIAGNOSTIC_ONLY"' in body
    assert '"qualification_fill_model": "CONSERVATIVE_BBO_DEPTH_TAPE"' in body


def test_fly_health_probe_is_liveness_only() -> None:
    body = _function_source("health")
    assert '"probe_contract": "PROCESS_LIVENESS_ONLY"' in body
    assert '"detail_endpoint": "/api/status"' in body
    assert "_strategy_progress_health_snapshot" not in body
    assert "can_open_live_entry" not in body


def test_fresh_session_uses_signed_epoch_cutoff_across_bot_restart() -> None:
    fn = _isolated_function(
        "_showcase_trade_session_start",
        {
            "state": {"fresh_collection_mode": True},
            "_load_research_session_meta": lambda: {
                "fresh_collection_start_time": 1_000.0
            },
            "bot_start_time": 2_000.0,
        },
    )
    assert fn() == 1_000.0


def test_trade_table_labels_signed_epoch_scope_instead_of_generic_session() -> None:
    assert "d.trade_scope === 'SIGNED_FRESH_EPOCH'" in BOT_SOURCE
    assert "current signed clean-epoch" in BOT_SOURCE
    assert "historical/all-history" in BOT_SOURCE


def test_successful_fresh_reset_updates_cached_signed_epoch_immediately() -> None:
    body = _function_source("api_fresh_epoch_reset")
    assert 'if result.get("ok"):' in body
    for field in (
        "fresh_epoch_id=epoch_id",
        "fresh_epoch_cutoff_utc=cutoff",
        "fresh_epoch_kind=kind",
        "trade_scope_cutoff_utc=cutoff",
        "trades=[]",
        "trade_count_session=0",
        "session_pnl_usd=0.0",
    ):
        assert field in body, f"fresh reset cache patch is missing {field}"


def test_continuous_saved_off_choice_overrides_legacy_direct_entry_flag() -> None:
    fn = _isolated_function(
        "continuous_ai_direct_entry_enabled",
        {
            "continuous_ai_research_enabled": lambda: False,
            "state_lock": None,
            "state": {"continuous_ai_direct_entry_enabled": True},
        },
    )
    assert fn() is False


def test_fresh_reset_removes_duplicate_intent_audit_and_rotations() -> None:
    wipe_paths = _function_source("research_wipe_file_paths")
    rotated_paths = _function_source("_research_wipe_rotated_jsonl_paths")

    assert "DUPLICATE_INTENT_AUDIT_FILE" in wipe_paths
    assert "DUPLICATE_INTENT_AUDIT_FILE" in rotated_paths
