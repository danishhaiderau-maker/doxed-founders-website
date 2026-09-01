"""Source-level contract checks for the /api/relay-execution-state snapshot.

These tests guard against regression of the relay-push and DDollar-gate
telemetry that the lightweight money-path snapshot must surface. The full
/api/relay-state endpoint exposes both via build_state_integrity(); past
audit workers who only read /api/relay-execution-state reported the fields
as "missing" because the slim snapshot did not mirror them. The contract
below locks the slim snapshot to keep including both fields.

We use AST source inspection rather than executing the snapshot builder
because bot.py imports ccxt/pandas/numpy and a live Bitfinex WS environment
at module load — neither appropriate for focused unit tests.
"""

from __future__ import annotations

import ast
import json
import threading
import time
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)
WORKFLOW_SOURCE = (BOT_PATH.parents[2] / ".github" / "workflows" / "fly-bot-deploy.yml").read_text(encoding="utf-8")


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_active_dashboard_overlay_refreshes_nested_manual_pause_label() -> None:
    """A paused-built presentation cache must not claim pause after resume."""
    func = _function("_api_state_cache_refresher_loop")
    body_src = ast.get_source_segment(BOT_SOURCE, func)
    assert body_src is not None
    assert 'paused_shadow_stats["manual_pause_active"] = bool(' in body_src
    assert 'relay.get("manual_admin_pause")' in body_src
    assert 'relay.get("execution_reason") == "ADMIN_MANUAL"' in body_src


def test_relay_execution_state_snapshot_surfaces_relay_push_summary() -> None:
    """state_integrity in the slim snapshot must include a relay_push block.

    The block must surface last_ok (the field audit checklists reference) and
    recent_deliveries_count (a faithful liveness summary). The raw history
    list is intentionally not duplicated here — /api/relay-state remains the
    detailed audit trail — but the headline health fields must be present so
    dashboards reading only /api/relay-execution-state can confirm delivery
    health without a second authenticated round-trip.
    """
    func = _function("_build_relay_execution_state_snapshot")
    body_src = ast.get_source_segment(BOT_SOURCE, func)
    assert body_src is not None, "_build_relay_execution_state_snapshot missing"

    # The slim state_integrity must reference relay_push state.
    assert '"relay_push"' in body_src, (
        "state_integrity in _build_relay_execution_state_snapshot must surface "
        'a "relay_push" block — see TASK 2 of the audit fix.'
    )
    assert "_relay_push_state" in body_src, (
        "relay_push summary must read from the canonical _relay_push_state "
        "module-level tracker, not a fabricated default."
    )
    assert '"last_ok"' in body_src, (
        "relay_push summary must surface last_ok — this is the field "
        "verification checklists reference."
    )
    assert "_relay_delivery_history_snapshot" in body_src, (
        "relay_push summary must derive recent_deliveries_count from "
        "_relay_delivery_history_snapshot so the count is faithful."
    )


def test_relay_execution_state_snapshot_surfaces_ddollar_gate_state() -> None:
    """state_integrity in the slim snapshot must include the DDollar gate.

    The block must surface `passed` (the field audit checklists reference) and
    must read it from bitfinex_live_executor.ddollar_gate_status() so the
    slim snapshot can never disagree with the authoritative gate used by the
    live entry path.
    """
    func = _function("_build_relay_execution_state_snapshot")
    body_src = ast.get_source_segment(BOT_SOURCE, func)
    assert body_src is not None

    assert '"ddollar_gate"' in body_src, (
        "state_integrity in _build_relay_execution_state_snapshot must surface "
        'a "ddollar_gate" block — see TASK 2 of the audit fix.'
    )
    assert "ddollar_gate_status" in body_src, (
        "ddollar_gate summary must call ddollar_gate_status() from "
        "bitfinex_live_executor — never invent a default passed/failed value."
    )


def test_relay_push_summary_is_read_only() -> None:
    """Adding telemetry must not mutate the canonical relay-push tracker.

    The snapshot builder is read-only w.r.t. money state. Guard that we do
    not assign into _relay_push_state or call its recording helpers inside
    the snapshot builder. Reads (`_relay_push_state["seq"]`) are fine.
    """
    func = _function("_build_relay_execution_state_snapshot")
    body_src = ast.get_source_segment(BOT_SOURCE, func)
    assert body_src is not None

    # Mutation patterns: assignment into the tracker, or appending to its
    # history deque. A bare `_relay_push_state["seq"]` on the RHS of an
    # assignment is a read and is intentionally NOT matched here.
    forbidden_writers = (
        "_relay_push_state[",  # present only if a write occurs in this builder
        "_record_relay_delivery(",  # appends to history
        "_relay_push_history.append(",  # direct mutation of history deque
    )
    # The first pattern is too strict if we read _relay_push_state["seq"].
    # Tighten: forbid only assignments by checking for `_relay_push_state[`
    # immediately followed by an identifier and then `] =`.
    import re
    write_pattern = re.compile(r'_relay_push_state\[[^\]]+\]\s*=')
    assert write_pattern.search(body_src) is None, (
        "_build_relay_execution_state_snapshot must not assign into "
        "_relay_push_state — telemetry is read-only."
    )
    for pattern in forbidden_writers[1:]:
        assert pattern not in body_src, (
            f"_build_relay_execution_state_snapshot must not mutate relay push "
            f"state via '{pattern}' — telemetry is read-only."
        )


def test_ddollar_gate_summary_cannot_invent_passed_value() -> None:
    """The DDollar summary must not hard-code a passed/failed boolean.

    It must always come from ddollar_gate_status(). A future refactor that
    inlines `passed: True` to silence a noisy audit would silently bypass
    the gate; this test fails closed if the live gate call is removed.
    """
    func = _function("_build_relay_execution_state_snapshot")
    body_src = ast.get_source_segment(BOT_SOURCE, func)
    assert body_src is not None

    forbidden_fabrications = (
        '"passed": True',
        '"passed": False',
        "'passed': True",
        "'passed': False",
    )
    for pattern in forbidden_fabrications:
        assert pattern not in body_src, (
            f"_build_relay_execution_state_snapshot must not hard-code "
            f"{pattern!r} — the gate value must come from ddollar_gate_status()."
        )


def test_execution_snapshot_publisher_assigns_one_monotonic_sequence() -> None:
    """Every immutable execution payload needs a durable audit sequence."""
    func = _function("_publish_relay_execution_snapshot")
    body_src = ast.get_source_segment(BOT_SOURCE, func)
    assert body_src is not None

    assert "global _RELAY_EXECUTION_SNAPSHOT_SEQ" in body_src
    assert "_RELAY_EXECUTION_SNAPSHOT_SEQ += 1" in body_src
    assert 'payload["snapshot_seq"] = _RELAY_EXECUTION_SNAPSHOT_SEQ' in body_src
    assert 'integrity["snapshot_seq"] = _RELAY_EXECUTION_SNAPSHOT_SEQ' in body_src
    assert body_src.index('_RELAY_EXECUTION_SNAPSHOT_SEQ += 1') < body_src.index('json.dumps('), (
        "snapshot sequence must be embedded before the immutable response body is serialized"
    )


def _publisher_namespace(builder):
    selected = [
        _function("_invalidate_relay_execution_snapshot"),
        _function("_publish_relay_execution_snapshot"),
    ]
    namespace = {
        "json": json, "time": time,
        "_RELAY_EXECUTION_CACHE_LOCK": threading.Lock(),
        "_RELAY_EXECUTION_REFRESH_LOCK": threading.Lock(),
        "_RELAY_EXECUTION_CACHE_PAYLOAD": {"old": True},
        "_RELAY_EXECUTION_CACHE_BODY": b"old",
        "_RELAY_EXECUTION_CACHE_AT": 1.0,
        "_RELAY_EXECUTION_SNAPSHOT_SEQ": 0,
        "_RELAY_EXECUTION_MONEY_STATE_GENERATION": 0,
        "_build_relay_execution_state_snapshot": builder,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def test_inflight_pre_mutation_snapshot_cannot_republish_after_invalidation() -> None:
    namespace = None

    def stale_builder():
        namespace["_invalidate_relay_execution_snapshot"]()
        return {"money_state_generation": 0, "state_integrity": {}}

    namespace = _publisher_namespace(stale_builder)
    assert namespace["_publish_relay_execution_snapshot"]() is None
    assert namespace["_RELAY_EXECUTION_MONEY_STATE_GENERATION"] == 1
    assert namespace["_RELAY_EXECUTION_CACHE_PAYLOAD"] is None
    assert namespace["_RELAY_EXECUTION_CACHE_BODY"] is None


def test_generation_matching_snapshot_publishes_immutable_authority() -> None:
    namespace = _publisher_namespace(
        lambda: {"money_state_generation": 0, "orders": [], "positions": [], "state_integrity": {}}
    )
    payload = namespace["_publish_relay_execution_snapshot"]()
    assert payload["money_state_generation"] == 0
    assert namespace["_RELAY_EXECUTION_CACHE_PAYLOAD"] is payload
    assert json.loads(namespace["_RELAY_EXECUTION_CACHE_BODY"])["positions"] == []


def test_fresh_route_and_confirmed_admin_mutations_are_generation_fenced() -> None:
    route = ast.get_source_segment(BOT_SOURCE, _function("api_relay_execution_state"))
    cancel = ast.get_source_segment(BOT_SOURCE, _function("api_cancel_showcase_pending_order"))
    phantom = ast.get_source_segment(BOT_SOURCE, _function("api_reconcile_phantom_cancel"))
    assert route and cancel and phantom
    assert 'request.args.get("fresh")' in route
    assert "if not _admin_authed()" in route
    assert "_publish_relay_execution_snapshot() is None" in route
    assert "generation_matches" in route and "503" in route
    assert "_invalidate_relay_execution_snapshot()" in cancel
    assert cancel.index('if not result.get("finalized")') < cancel.index("_invalidate_relay_execution_snapshot()")
    assert phantom.count("_invalidate_relay_execution_snapshot()") == 2
    assert "len(live_matches) > 1" in phantom
    assert "has_real_marker" in phantom


def test_guarded_deploy_requires_post_mutation_generation_and_retries_only_503() -> None:
    assert "def fresh_exposure(minimum_generation=None):" in WORKFLOW_SOURCE
    assert "if exc.code != 503:" in WORKFLOW_SOURCE
    assert 'request_json("/api/relay-execution-state?fresh=1")' in WORKFLOW_SOURCE
    assert "required_generation = max(required_generation or 0, generation)" in WORKFLOW_SOURCE
    assert "generation <= round_generation" in WORKFLOW_SOURCE
    assert "flat relay authority predates maintenance mutations" in WORKFLOW_SOURCE
    assert "if not orders and not positions:" in WORKFLOW_SOURCE


def test_counterfactual_policy_and_replay_evidence_fail_closed() -> None:
    """Optimization evidence must carry immutable policy and completion proof."""
    policy_func = _function("get_exit_config_snapshot")
    policy_src = ast.get_source_segment(BOT_SOURCE, policy_func)
    assert policy_src is not None
    for field in (
        '"policy_snapshot_schema"',
        '"policy_version"',
        '"hard_stop_margin_pct"',
        '"thesis_fast_exit_unreal_pct"',
        '"thesis_mfe_protect_pct"',
        '"trail_ladder"',
    ):
        assert field in policy_src

    replay_func = _function("dump_replay")
    replay_src = ast.get_source_segment(BOT_SOURCE, replay_func)
    assert replay_src is not None
    assert '"replay_complete": replay_complete' in replay_src
    assert '"post_exit_complete": post_exit_complete' in replay_src
    assert '"terminal_provenance"' in replay_src
    assert "and required_post_exit_tick is not None" in replay_src
    assert '"best_bid" if' in replay_src and 'else "best_ask"' in replay_src

    evidence_func = _function("build_counterfactual_observability_fields")
    evidence_src = ast.get_source_segment(BOT_SOURCE, evidence_func)
    assert evidence_src is not None
    assert 'replay.get("replay_complete") is True' in evidence_src
    assert 'fields["analysis_cohorts"] = assessment' in evidence_src
    assert 'REAL_COPY_PARAMETER_OPTIMISATION' in evidence_src
    assert '"analysis_eligible": False' in evidence_src
    assert '"ACTUAL_BITFINEX_PNL_MISSING"' in evidence_src
    assert '"bitfinex_evidence": bitfinex_evidence' in evidence_src
    assert '"required_post_exit_horizons_complete"' in evidence_src


if __name__ == "__main__":
    test_relay_execution_state_snapshot_surfaces_relay_push_summary()
    test_relay_execution_state_snapshot_surfaces_ddollar_gate_state()
    test_relay_push_summary_is_read_only()
    test_ddollar_gate_summary_cannot_invent_passed_value()
    test_execution_snapshot_publisher_assigns_one_monotonic_sequence()
    print("Relay-execution-state telemetry contract checks passed")
