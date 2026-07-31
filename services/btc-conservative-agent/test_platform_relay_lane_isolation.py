"""Focused fail-closed checks for the Fly paper-source -> live relay boundary."""

from __future__ import annotations

import ast
from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _compile_function(name: str, namespace: dict) -> object:
    node = _function(name)
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), f"<{name}-test>", "exec"),
        namespace,
    )
    return namespace[name]


def test_relay_lane_resolution_requires_matching_allowlisted_prefix() -> None:
    namespace = {
        "PLATFORM_RELAY_TRADE_PREFIX_LANES": {"cont": "CONTINUOUS"},
    }
    resolve = _compile_function("_platform_relay_lane_for_event", namespace)

    assert resolve("cont-abc123", "CONTINUOUS") == "CONTINUOUS"
    assert resolve("cont-abc123", None) == "CONTINUOUS"
    assert resolve("cont-abc123", "TYPE_B_HUNTER_V1") == ""
    assert resolve("tbhv1-paper", "TYPE_B_HUNTER_V1") == ""
    assert resolve("tbhv1-paper", "CONTINUOUS") == ""
    assert resolve("bareuuid", "CONTINUOUS") == ""


def test_type_b_chase_and_close_events_stop_before_network_post() -> None:
    warnings: list[str] = []

    class QuietLogger:
        def warning(self, message: str) -> None:
            warnings.append(message)

    class FakeEnvironment:
        @staticmethod
        def getenv(name: str) -> str:
            return {
                "SHOWCASE_RELAY_WEBHOOK_URL": "https://example.invalid/relay",
                "BOT_CONTROL_SECRET": "control",
                "SHOWCASE_WEBHOOK_SECRET": "webhook",
            }.get(name, "")

    namespace = {
        "PLATFORM_RELAY_TRADE_PREFIX_LANES": {"cont": "CONTINUOUS"},
        "PLATFORM_RELAY_ELIGIBLE_LANES": frozenset({"CONTINUOUS"}),
        "is_active_dashboard_owner": lambda: True,
        "_dashboard_owner_metadata": lambda: {},
        "utc_iso": lambda: "2026-07-31T00:00:00Z",
        "logger": QuietLogger(),
        "os": FakeEnvironment(),
    }
    _compile_function("_platform_relay_lane_for_event", namespace)
    push = _compile_function("_push_showcase_relay_event", namespace)

    push(
        "LIMIT_UPDATED",
        "tbhv1-paper",
        {
            "research_lane": "TYPE_B_HUNTER_V1",
            "direction": "LONG",
            "limit_price": 63_900,
            "entry_limit_policy": "deterministic_0.1pct_offset_v1",
            "executable": True,
        },
    )
    push(
        "POSITION_CLOSED",
        "tbhv1-paper",
        {
            "research_lane": "TYPE_B_HUNTER_V1",
            "direction": "LONG",
            "exit_price": 64_100,
        },
    )

    assert len(warnings) == 2
    assert all("blocked non-relay lifecycle" in message for message in warnings)


def test_every_relay_lifecycle_path_is_wired_to_lane_metadata() -> None:
    push_source = ast.get_source_segment(
        BOT_SOURCE,
        _function("_push_showcase_relay_event"),
    )
    emit_source = ast.get_source_segment(BOT_SOURCE, _function("emit_signal_webhook"))
    close_source = ast.get_source_segment(BOT_SOURCE, _function("close_position"))

    assert 'event in ("LIMIT_UPDATED", "POSITION_CLOSED")' in push_source
    assert "_platform_relay_lane_for_event" in push_source
    assert 'payload["research_lane"] = relay_lane' in push_source
    assert "_platform_relay_lane_for_event" in emit_source
    assert '"research_lane": (' in close_source
    assert 'pos.get("research_lane")' in close_source


if __name__ == "__main__":
    test_relay_lane_resolution_requires_matching_allowlisted_prefix()
    test_type_b_chase_and_close_events_stop_before_network_post()
    test_every_relay_lifecycle_path_is_wired_to_lane_metadata()
    print("Platform relay lane isolation checks passed")
