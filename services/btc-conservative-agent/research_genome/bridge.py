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
    def __init__(
        self,
        base_dir: str,
        *,
        dataset_epoch: str | None = None,
        deployed_revision: str | None = None,
        tile_config_signature: str | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.bus = ResearchEventBus()
        self.store = ResearchStore(base_dir)
        register_all_recorders(self.bus, self.store)
        self._current_env_id: str | None = None
        self._current_mkt_id: str | None = None
        self._current_dec_id: str | None = None
        self._generation_identity: Dict[str, Any] | None = None
        identity_values = (dataset_epoch, deployed_revision, tile_config_signature)
        if any(identity_values):
            if not all(identity_values):
                raise ValueError("GENOME_GENERATION_IDENTITY_INCOMPLETE")
            self.bind_generation_identity(
                dataset_epoch=str(dataset_epoch),
                deployed_revision=str(deployed_revision),
                tile_config_signature=str(tile_config_signature),
            )
        logger.info("[GENOME] bridge initialized db=%s", self.store.db_path)

    def bind_generation_identity(
        self,
        *,
        dataset_epoch: str,
        deployed_revision: str,
        tile_config_signature: str,
    ) -> Dict[str, Any]:
        """Bind one immutable collection generation to this Genome database."""
        revision = str(deployed_revision or "").strip().lower()
        if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
            raise ValueError("GENOME_DEPLOYED_REVISION_NOT_EXACT_FULL_SHA")
        identity = self.store.record_generation_identity(
            dataset_epoch=dataset_epoch,
            deployed_revision=revision,
            tile_config_signature=tile_config_signature,
        )
        counts = self.store.stats()
        self.store.record_ingestion_status(
            generation_id=identity["generation_id"],
            status="BRIDGE_INITIALIZED",
            row_count=sum(counts.values()),
            opportunity_count=None,
            detail={
                "count_semantics": "genome_table_rows_plus_research_events",
                "opportunity_count_status": "UNAVAILABLE_IN_GENOME_STORE",
                "snapshot_counts": counts,
            },
        )
        self._generation_identity = self.store.generation_identity(identity["generation_id"])
        return dict(self._generation_identity or identity)

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
        ai_confidence: int | None = None,
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
            "generation_identity": self._generation_identity,
            **self.store.stats(),
        }

    def reset_research_store(self, *, destructive: bool = False,
                             deletion_receipt_path=None, quiescent: bool = False,
                             recovery_states=None) -> Dict[str, Any]:
        if destructive is not False and destructive is not True:
            raise ValueError("destructive must be an explicit boolean")
        result = (self.store.reset(
            destructive=True, deletion_receipt_path=deletion_receipt_path,
            quiescent=quiescent, recovery_states=recovery_states,
        ) if destructive else self.store.reset())
        self._generation_identity = None
        self._current_env_id = None
        self._current_mkt_id = None
        self._current_dec_id = None
        logger.info("[GENOME] Fresh Collection reset completed bytes=%s", result["removed_bytes"])
        return result


def init_genome_bridge(
    base_dir: str | None = None,
    *,
    dataset_epoch: str | None = None,
    deployed_revision: str | None = None,
    tile_config_signature: str | None = None,
) -> GenomeBridge:
    global _bridge
    if base_dir:
        root = os.path.abspath(base_dir)
    else:
        volume_root = (os.getenv("BOT_DATA_DIR") or "").strip()
        if volume_root:
            # Fly's authenticated mirror publishes the runtime directory as
            # its canonical root. Keep the legacy Genome source inside that
            # persistent/syncable boundary rather than beside deployed code.
            root = os.path.join(os.path.abspath(volume_root), "runtime")
        else:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(root, exist_ok=True)
    _bridge = GenomeBridge(
        root,
        dataset_epoch=dataset_epoch,
        deployed_revision=deployed_revision,
        tile_config_signature=tile_config_signature,
    )
    return _bridge


def get_genome_bridge() -> GenomeBridge | None:
    return _bridge
