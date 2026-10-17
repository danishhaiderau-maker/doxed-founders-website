"""D8 data-version stamp: which fresh-start epoch a stored row belongs to.

``fse-<epoch_ts>`` read from ``fresh_start_epoch.json`` (the file POST /api/monitor/fresh-start writes). Rows
written by the Bitfinex book / trades / derivative-status / venue-bbo collector carry it as ``dv`` (short on
purpose: the book stream is ~1 row per second); closed paper trades carry it as ``data_version``. Nothing is
stamped until an epoch exists, so legacy rows stay without the key. Readers ignore unknown keys.
"""

from __future__ import annotations

import json
import os
import time

FILE = "fresh_start_epoch.json"


def id_from_epoch(doc) -> str | None:
    try:
        ts = float((doc or {}).get("epoch_ts") or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    return f"fse-{int(ts)}" if ts > 0 else None


class Stamp:
    """Cheap per-second lookup: one ``stat`` per call, re-reads only when the file changed."""

    def __init__(self, directory: str, clock=time.time) -> None:
        self.path = os.path.join(directory, FILE)
        self._key = None
        self._value: str | None = None
        self._clock = clock

    def current(self) -> str | None:
        try:
            st = os.stat(self.path)
        except OSError:
            self._key, self._value = None, None
            return None
        key = (st.st_mtime_ns, st.st_size)
        if key != self._key:
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._value = id_from_epoch(json.load(fh))
            except (OSError, ValueError):
                self._value = None
            self._key = key
        return self._value
