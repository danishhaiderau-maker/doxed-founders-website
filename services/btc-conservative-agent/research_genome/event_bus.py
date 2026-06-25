"""In-process research event bus — decouples execution from recorders."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from research_genome.events import EVENT_VERSIONS, SCHEMA_VERSION

logger = logging.getLogger(__name__)

Subscriber = Callable[[Dict[str, Any]], None]


class ResearchEventBus:
    def __init__(self) -> None:
        self._subs: Dict[str, List[Subscriber]] = defaultdict(list)
        self._lock = threading.Lock()
        self._seq = 0

    @property
    def sequence(self) -> int:
        return self._seq

    def subscribe(self, event_name: str, handler: Subscriber) -> None:
        with self._lock:
            self._subs[event_name.upper()].append(handler)

    def subscribe_all(self, handler: Subscriber) -> None:
        with self._lock:
            self._subs["*"].append(handler)

    def emit(self, event_name: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        name = str(event_name or "").upper()
        version = EVENT_VERSIONS.get(name, 1)
        with self._lock:
            self._seq += 1
            seq = self._seq
        event = {
            "event_name": name,
            "event_version": version,
            "schema_version": SCHEMA_VERSION,
            "bus_seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            **(payload or {}),
        }
        handlers = []
        with self._lock:
            handlers = list(self._subs.get(name, [])) + list(self._subs.get("*", []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.warning("[GENOME] recorder error on %s: %s", name, exc)
        return event
