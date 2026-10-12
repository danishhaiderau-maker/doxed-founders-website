"""HM-BATCH2: 7-day 1 s tape store on the Fly volume (hourly gzip JSONL; pruned after 8 days).

``append`` is O(1) and lock-cheap (called from the tape loop); a lazily started daemon thread
flushes every few seconds. Each flush appends one gzip member, so files stay valid gzip.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping

RETAIN_SEC = 8 * 86400
FLUSH_SEC = 5.0
MAX_PENDING = 7200


class TapeArchive:
    def __init__(self, root_fn: Callable[[], Path], *, clock: Callable[[], float] = time.time) -> None:
        self._root_fn, self._clock = root_fn, clock
        self._pending: deque = deque(maxlen=MAX_PENDING)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.stats = {"written": 0, "dropped": 0, "errors": 0, "last_error": None, "last_flush_ts": None}

    def root(self) -> Path:
        return Path(self._root_fn()) / "tape_1s"

    def append(self, row: Mapping[str, Any], fields: tuple = ()) -> None:
        if len(self._pending) >= MAX_PENDING:
            self.stats["dropped"] += 1
        keep = {k: row.get(k) for k in ("bucket_ts", *fields)} if fields else dict(row)
        self._pending.append(keep)
        if self._thread is None:
            with self._lock:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="tape-archive", daemon=True)
                    self._thread.start()

    def _run(self) -> None:
        last_prune = 0.0
        while True:
            time.sleep(FLUSH_SEC)
            try:
                self.flush()
                if self._clock() - last_prune > 3600:
                    self.prune()
                    last_prune = self._clock()
            except Exception as exc:  # never kill the thread
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(exc).__name__}: {exc}"[:200]

    def flush(self) -> int:
        rows = []
        while self._pending:
            rows.append(self._pending.popleft())
        if not rows:
            return 0
        root = self.root()
        root.mkdir(parents=True, exist_ok=True)
        by_hour: dict = {}
        for r in rows:
            ts = float(r.get("bucket_ts") or r.get("ts") or self._clock())
            by_hour.setdefault(time.strftime("%Y%m%d%H", time.gmtime(ts)), []).append(r)
        for hour, items in by_hour.items():
            data = "".join(json.dumps(x, separators=(",", ":"), default=str) + "\n" for x in items)
            with open(root / f"{hour}.jsonl.gz", "ab") as fh:
                fh.write(gzip.compress(data.encode("utf-8"), compresslevel=6))
        self.stats["written"] += len(rows)
        self.stats["last_flush_ts"] = self._clock()
        return len(rows)

    def prune(self) -> int:
        cutoff = time.strftime("%Y%m%d%H", time.gmtime(self._clock() - RETAIN_SEC))
        n = 0
        for f in self.root().glob("*.jsonl.gz"):
            if f.name[:10] < cutoff:
                f.unlink(missing_ok=True)
                n += 1
        return n

    def oldest_ts(self) -> float | None:
        try:
            names = sorted(f.name[:10] for f in self.root().glob("*.jsonl.gz"))
        except OSError:
            return None
        if not names:
            return None
        import calendar
        return float(calendar.timegm(time.strptime(names[0], "%Y%m%d%H")))

    def snapshot(self) -> dict:
        try:
            files = list(self.root().glob("*.jsonl.gz"))
            size = sum(f.stat().st_size for f in files)
        except OSError:
            files, size = [], 0
        return {**self.stats, "files": len(files), "bytes": size, "oldest_ts": self.oldest_ts(),
                "pending": len(self._pending), "root": str(self.root())}
