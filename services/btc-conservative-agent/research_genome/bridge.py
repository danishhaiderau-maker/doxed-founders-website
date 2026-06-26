"""Bot integration bridge — thin hooks from execution into the genome event bus."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from research_genome.event_bus import ResearchEventBus
from research_genome.ids import new_decision_id, new_environment_id, new_market_genome_id
from research_genome.recorders import register_all_recorders
from research_genome.store import ResearchStore

logger = logging.getLogger(__name__)

_bridge: Optional["GenomeBridge"] = None


def _melbourne_now() -> datetime:
    return datetime.now(ZoneInfo("Australia/Melbourne"))


def build_environment_context(ts: datetime | None = None) -> Dict[str, Any]:
    now = ts or _melbourne_now()
    utc = now.astimezone(timezone.utc)
    hour_utc = utc.hour
    dow = now.weekday()
    is_weekend = dow >= 5
    if 0 <= hour_utc < 8:
        session = "ASIA"
    elif 8 <= hour_utc < 13:
        session = "OVERLAP"
    elif 13 <= hour_utc < 21:
        session = "US"
    else:
        session = "EUROPE"
    day = now.day
    month = now.month
    month_end = day >= 28
    quarter_end = month in (3, 6, 9, 12) and day >= 26
    funding_window = hour_utc in (0, 8, 16)
    return {
        "is_weekend": is_weekend,
        "day_of_week": dow,
        "hour_utc": hour_utc,
        "trading_session": session,
        "funding_window": funding_window,
        "month_end": month_end,
        "quarter_end": quarter_end,
    }


class GenomeBridge:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.bus = ResearchEventBus()
        self.store = ResearchStore(base_dir)
        register_all_recorders(self.bus, self.store)
        self._current_env_id: str | None = None
        self._current_mkt_id: str | None = None
        self._current_dec_id: str | None = None
        logger.info("[GENOME] bridge initialized db=%s", self.store.db_path)

    @property
    def bus_seq(self) -> int:
        return self.bus.sequence

    def emit(self, event_name: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.bus.emit(event_name, payload)

    def on_ai_scan_complete(self, market: Dict[str, Any]) -> Dict[str, str]:
        env_id = new_environment_id()
        mkt_id = new_market_genome_id()
        self._current_env_id = env_id
        self._current_mkt_id = mkt_id
        env_ctx = build_environment_context()
        self.emit("AI_SCAN_COMPLETE", {
            "environment_id": env_id,
            "market_genome_id": mkt_id,
            **env_ctx,
            **market,
        })
        return {"environment_id": env_id, "market_genome_id": mkt_id}

    def on_ai_decision(
        self,
        approved: bool,
        *,
        trade_id: str = "",
        ai_confidence: int = 0,
        direction: str = "",
        block_reason: str = "",
        research_lane: str = "",
        extra: Dict[str, Any] | None = None,
    ) -> str:
        dec_id = new_decision_id()
        self._current_dec_id = dec_id
        event = "AI_APPROVED" if approved else "AI_REJECTED"
        payload = {
            "decision_id": dec_id,
            "market_genome_id": self._current_mkt_id,
            "environment_id": self._current_env_id,
            "trade_id": trade_id,
            "ai_confidence": ai_confidence,
            "direction": direction,
            "block_reason": block_reason,
            "research_lane": research_lane,
            **(extra or {}),
        }
        self.emit(event, payload)
        return dec_id

    def on_execution_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        body = {
            "decision_id": self._current_dec_id,
            "market_genome_id": self._current_mkt_id,
            **payload,
        }
        self.emit(event_name, body)

    def on_position_closed(self, trade: Dict[str, Any]) -> None:
        body = {
            "decision_id": self._current_dec_id or trade.get("decision_id"),
            "market_genome_id": self._current_mkt_id,
            **trade,
        }
        self.emit("POSITION_CLOSED", body)

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "bus_seq": self.bus_seq,
            "db_path": self.store.db_path,
            **self.store.stats(),
        }


def init_genome_bridge(base_dir: str | None = None) -> GenomeBridge:
    global _bridge
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(root)
    _bridge = GenomeBridge(root)
    return _bridge


def get_genome_bridge() -> GenomeBridge | None:
    return _bridge
