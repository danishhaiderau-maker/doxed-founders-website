"""Genome recorders — subscribe to event bus, write to research store."""
from __future__ import annotations

from typing import Any, Dict

from research_genome.event_bus import ResearchEventBus
from research_genome.ids import (
    new_decision_id,
    new_environment_id,
    new_execution_id,
    new_market_genome_id,
    new_trade_id,
)
from research_genome.store import ResearchStore


class BaseRecorder:
    layer = ""

    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def handle(self, event: Dict[str, Any]) -> None:
        raise NotImplementedError


class EnvironmentRecorder(BaseRecorder):
    layer = "environment"

    def handle(self, event: Dict[str, Any]) -> None:
        if event.get("event_name") != "AI_SCAN_COMPLETE":
            return
        env_id = event.get("environment_id") or new_environment_id()
        row = {"environment_id": env_id, **event}
        self.store.upsert_layer("environment", env_id, row)


class MarketRecorder(BaseRecorder):
    layer = "market"

    def handle(self, event: Dict[str, Any]) -> None:
        if event.get("event_name") != "AI_SCAN_COMPLETE":
            return
        mkt_id = event.get("market_genome_id") or new_market_genome_id()
        env_id = event.get("environment_id")
        row = {"market_genome_id": mkt_id, "environment_id": env_id, **event}
        self.store.upsert_layer("market", mkt_id, row, parent_id=env_id or "")


class DecisionRecorder(BaseRecorder):
    layer = "decision"

    def handle(self, event: Dict[str, Any]) -> None:
        name = event.get("event_name")
        if name not in ("AI_APPROVED", "AI_REJECTED"):
            return
        dec_id = event.get("decision_id") or new_decision_id()
        mkt_id = event.get("market_genome_id")
        decision = "APPROVE" if name == "AI_APPROVED" else "REJECT"
        row = {
            "decision_id": dec_id,
            "market_genome_id": mkt_id,
            "decision": decision,
            **event,
        }
        self.store.upsert_layer("decision", dec_id, row, parent_id=mkt_id or "")


class ExecutionRecorder(BaseRecorder):
    layer = "execution"

    _EVENTS = frozenset({
        "LIMIT_CREATED", "LIMIT_CHASED", "ORDER_FILLED",
        "ORDER_CANCELLED", "ORDER_EXPIRED",
    })

    def handle(self, event: Dict[str, Any]) -> None:
        if event.get("event_name") not in self._EVENTS:
            return
        exe_id = event.get("execution_id") or new_execution_id()
        dec_id = event.get("decision_id")
        row = {"execution_id": exe_id, "decision_id": dec_id, **event}
        self.store.upsert_layer("execution", exe_id, row, parent_id=dec_id or "")


class LifecycleRecorder(BaseRecorder):
    layer = "lifecycle"

    _EVENTS = frozenset({
        "POSITION_OPENED", "POSITION_UPDATED", "POSITION_CLOSED",
        "MFE_UPDATED", "MAE_UPDATED", "LADDER_STEP_ARMED", "LADDER_STEP_HIT",
        "STOP_UPDATED", "THESIS_CHANGED", "EXIT_TRIGGERED",
    })

    def handle(self, event: Dict[str, Any]) -> None:
        if event.get("event_name") not in self._EVENTS:
            return
        lifecycle_id = event.get("lifecycle_id") or f"lfc_{event.get('execution_id') or event.get('trade_id')}_{event.get('bus_seq')}"
        exe_id = event.get("execution_id")
        row = {"lifecycle_id": lifecycle_id, "execution_id": exe_id, **event}
        self.store.upsert_layer("lifecycle", lifecycle_id, row, parent_id=exe_id or "")


class TradeRecorder(BaseRecorder):
    layer = "trade"

    def handle(self, event: Dict[str, Any]) -> None:
        if event.get("event_name") != "POSITION_CLOSED":
            return
        trd_id = event.get("trade_id") or new_trade_id()
        dec_id = event.get("decision_id")
        row = {"trade_id": trd_id, "decision_id": dec_id, **event}
        self.store.upsert_layer("trade", trd_id, row, parent_id=dec_id or "")


def register_all_recorders(bus: ResearchEventBus, store: ResearchStore) -> None:
    recorders = [
        EnvironmentRecorder(store),
        MarketRecorder(store),
        DecisionRecorder(store),
        ExecutionRecorder(store),
        LifecycleRecorder(store),
        TradeRecorder(store),
    ]
    for rec in recorders:
        bus.subscribe_all(rec.handle)
    bus.subscribe_all(store.append_event)
