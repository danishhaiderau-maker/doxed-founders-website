"""Phantom-cancel must never zero paper-only / relay-ineligible or unsigned trades.

The gate is extracted from bot.py's AST (bot.py cannot be imported in unit
tests) and exercised against the real tile registry and relay outbox.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from combo_pathway_config import ACTIVE_TILE_REGISTRY
from relay_event_outbox import RelayEventOutbox

BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _gate(registry, relay_lanes, outbox):
    nodes = [
        node for node in BOT_TREE.body
        if (isinstance(node, ast.FunctionDef) and node.name == "phantom_cancel_refusal")
        or (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PHANTOM_CANCEL_ENTRY_EVENTS"
        )
    ]
    assert len(nodes) == 2
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "ACTIVE_TILE_REGISTRY": registry,
        "PLATFORM_RELAY_ELIGIBLE_LANES": frozenset(relay_lanes),
        "_normalize_lane_key": lambda obj: str(obj.get("research_lane") or "CONTINUOUS").upper(),
        "_relay_event_outbox": outbox,
    }
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["phantom_cancel_refusal"]


def _relay_capable_registry():
    """A hypothetical relay-eligible copy of the first tile (none exist today)."""
    registry = copy.deepcopy(ACTIVE_TILE_REGISTRY)
    lane = next(iter(registry))
    registry[lane].update({"paper_only": False, "platform_relay_eligible": True})
    return registry, lane, registry[lane]["id_prefix"]


def _ack(outbox, trade_id, event_type="ORDER_PLACED", intent_created=True):
    outbox._acks.append({
        "event_id": f"{trade_id}-{event_type}", "event_type": event_type,
        "trade_id": trade_id, "event_seq": 0, "payload_sha256": "x",
        "intent_created": intent_created,
    })


@pytest.mark.parametrize("lane", list(ACTIVE_TILE_REGISTRY))
def test_every_current_paper_only_tile_is_refused(tmp_path, lane) -> None:
    outbox = RelayEventOutbox(tmp_path / "outbox.json")
    prefix = ACTIVE_TILE_REGISTRY[lane]["id_prefix"]
    trade_id = f"{prefix}-abc123"
    _ack(outbox, trade_id)
    relay_lanes = {l for l, s in ACTIVE_TILE_REGISTRY.items() if s.get("platform_relay_eligible")}
    gate = _gate(ACTIVE_TILE_REGISTRY, relay_lanes, outbox)
    code, _detail = gate({"research_lane": lane, "trade_id": trade_id}, trade_id)
    assert code == "PHANTOM_CANCEL_RELAY_INELIGIBLE_LANE"


def test_registry_declares_no_relay_capable_tile_today() -> None:
    for spec in ACTIVE_TILE_REGISTRY.values():
        assert spec.get("paper_only") is True
        assert not spec.get("platform_relay_eligible")


@pytest.mark.parametrize("pos, code", [
    ({"research_lane": "CONTINUOUS"}, "PHANTOM_CANCEL_NON_REGISTRY_LANE"),
    ({}, "PHANTOM_CANCEL_NON_REGISTRY_LANE"),
    ({"research_lane": "AI_SCAN"}, "PHANTOM_CANCEL_NON_REGISTRY_LANE"),
])
def test_non_registry_lanes_are_refused(tmp_path, pos, code) -> None:
    gate = _gate(ACTIVE_TILE_REGISTRY, set(), RelayEventOutbox(tmp_path / "o.json"))
    assert gate(pos, "cont-abc")[0] == code


def test_relay_capable_tile_requires_signed_intent_created_after_arming(tmp_path) -> None:
    registry, lane, prefix = _relay_capable_registry()
    outbox = RelayEventOutbox(tmp_path / "outbox.json")
    gate = _gate(registry, {lane}, outbox)
    trade_id = f"{prefix}-live01"
    pos = {"research_lane": lane, "trade_id": trade_id}

    assert gate(pos, trade_id)[0] == "PHANTOM_CANCEL_NO_SIGNED_RELAY_INTENT"
    _ack(outbox, trade_id, intent_created=False)
    assert gate(pos, trade_id)[0] == "PHANTOM_CANCEL_NO_SIGNED_RELAY_INTENT"
    _ack(outbox, trade_id, event_type="POSITION_OPENED", intent_created=True)
    assert gate(pos, trade_id)[0] == "PHANTOM_CANCEL_NO_SIGNED_RELAY_INTENT"
    _ack(outbox, trade_id, event_type="ORDER_PLACED", intent_created=True)
    assert gate(pos, trade_id) is None


def test_relay_capable_tile_rejects_foreign_namespace_and_ineligible_position(tmp_path) -> None:
    registry, lane, prefix = _relay_capable_registry()
    outbox = RelayEventOutbox(tmp_path / "outbox.json")
    _ack(outbox, "zzz-1")
    _ack(outbox, f"{prefix}-2")
    gate = _gate(registry, {lane}, outbox)
    assert gate({"research_lane": lane}, "zzz-1")[0] == "PHANTOM_CANCEL_TRADE_NAMESPACE_MISMATCH"
    assert gate({"research_lane": lane, "relay_eligible": False}, f"{prefix}-2")[0] == (
        "PHANTOM_CANCEL_POSITION_NOT_RELAY_ELIGIBLE"
    )


def test_missing_outbox_fails_closed() -> None:
    registry, lane, prefix = _relay_capable_registry()
    gate = _gate(registry, {lane}, None)
    assert gate({"research_lane": lane}, f"{prefix}-1")[0] == "PHANTOM_CANCEL_NO_SIGNED_RELAY_INTENT"


def test_outbox_ack_records_platform_intent_creation(tmp_path) -> None:
    outbox = RelayEventOutbox(tmp_path / "outbox.json")
    for trade_id, created in (("t-created", True), ("t-paused", None)):
        row = outbox.enqueue_next({"event": "ORDER_PLACED", "trade_id": trade_id}, 0)
        receipt = {
            "durable_ack": {
                key: row[key] for key in ("event_id", "event_type", "trade_id", "event_seq", "payload_sha256")
            },
        }
        if created is not None:
            receipt["intentCreated"] = created
        assert outbox.acknowledge(row["event_id"], receipt) is True
    assert [a["intent_created"] for a in outbox.acknowledged_events("t-created")] == [True]
    assert [a["intent_created"] for a in outbox.acknowledged_events("t-paused")] == [False]
    assert outbox.acknowledged_events("unknown") == []
    reloaded = RelayEventOutbox(tmp_path / "outbox.json")
    assert reloaded.acknowledged_events("t-created")[0]["intent_created"] is True
