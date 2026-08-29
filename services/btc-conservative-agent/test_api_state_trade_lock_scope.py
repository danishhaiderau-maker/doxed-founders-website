"""Regression contract for bounded dashboard money-state snapshots."""

import ast
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))


def _function(name):
    return next(
        node for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _trade_lock_regions(function_name):
    function = _function(function_name)
    return [
        node for node in ast.walk(function)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Name)
            and item.context_expr.id == "trade_lock"
            for item in node.items
        )
    ]


def _called_names(node):
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def test_disk_relay_evidence_and_expensive_enrichment_are_outside_trade_lock():
    forbidden = {
        "_load_dashboard_trade_enrichment",
        "_platform_relay_evidence_index",
        "_pure_platform_relay_evidence_index",
        "_enrich_dashboard_trade_rows",
        "_pure_snapshot_with_platform_relay_evidence",
        "_relay_fidelity_trade_row",
        "accrue_position_funding",
    }
    for function_name in (
        "_build_api_state_snapshot",
        "_build_relay_execution_state_snapshot",
        "api_relay_state",
    ):
        for region in _trade_lock_regions(function_name):
            assert not (forbidden & _called_names(region)), function_name
            assert not any(isinstance(child, (ast.Import, ast.ImportFrom)) for child in ast.walk(region))


def test_locked_trade_snapshot_is_bounded_and_has_no_disk_or_hash_operations():
    body = ast.get_source_segment(SOURCE, _function("_snapshot_trade_rows_locked"))
    assert "_DASHBOARD_TRADES_MAX" in body
    assert "copy.deepcopy(src)" in body
    for forbidden in ("open(", "read_text", "read_bytes", "sha", "import "):
        assert forbidden not in body


def test_snapshot_builders_publish_phase_timings_and_lock_diagnostics():
    dashboard = ast.get_source_segment(SOURCE, _function("_build_api_state_snapshot"))
    relay = ast.get_source_segment(SOURCE, _function("_build_relay_execution_state_snapshot"))
    for body in (dashboard, relay):
        for phase in (
            '"relay_evidence_load"',
            '"trade_lock_wait"',
            '"trade_lock_hold"',
            '"post_lock_enrichment"',
        ):
            assert phase in body
        assert "trade_lock.diagnostics()" in body

    for phase in (
        '"initial_setup"',
        '"readiness_projection"',
        '"signal_history_projection"',
        '"metadata_research_projection"',
        '"position_order_projection"',
        '"branding_relay_projection"',
        '"epoch_policy_projection"',
        '"final_state_integrity"',
    ):
        assert phase in dashboard


def test_http_admission_telemetry_is_bounded_and_secret_safe():
    server = ast.get_source_segment(SOURCE, _function("_create_dashboard_server"))
    assert 'reason="dispatch_cap_full"' in server
    assert 'reason="class_cap_full"' in server
    assert 'reason="slow_handler"' in server
    assert '"GENERAL"' in server
    assert "_telemetry_log_interval_sec = 10.0" in server
    assert "_slow_request_log_sec = 2.0" in server
    # Only exact allowlisted paths are decoded for telemetry. An arbitrary raw
    # request target (which can contain query credentials) is never rendered.
    assert 'request_path.decode("ascii")' in server
    assert "head.decode" not in server
    assert "request.args" not in server
    assert "request.url" not in server
    assert "query_string" not in server


def test_enrichment_loader_runs_before_trade_lock_acquisition():
    for function_name in (
        "_build_api_state_snapshot",
        "_build_relay_execution_state_snapshot",
    ):
        body = ast.get_source_segment(SOURCE, _function(function_name))
        assert body.index("_load_dashboard_trade_enrichment()") < body.index("trade_lock.acquire(")
        locked = body[
            body.index("trade_lock.acquire("):
            body.index("trade_lock.release()")
        ]
        for forbidden in (
            "_load_dashboard_trade_enrichment(",
            "_enrich_dashboard_trade_rows(",
            "_pure_snapshot_with_platform_relay_evidence(",
            "_relay_fidelity_trade_row(",
            "accrue_position_funding(",
        ):
            assert forbidden not in locked, (function_name, forbidden)
