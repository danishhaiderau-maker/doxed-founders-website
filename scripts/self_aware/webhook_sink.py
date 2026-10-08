"""Durable alert outbox + optional webhook sink for ``selfaware.*`` transitions.

Reuses the crash-safe outbox idea from ``services/btc-conservative-agent/
relay_event_outbox.py``: an event is persisted (atomic append + fsync) BEFORE any
HTTP attempt, so a RED/AMBER transition is never lost on a crash mid-delivery.
The local ``alarms.jsonl`` written by ``alarms.flush`` remains the default and
authoritative sink; this webhook is an *additional*, optional push that is only
active when ``SELF_AWARE_WEBHOOK_URL`` is set.

Everything here is read-only with respect to trading: it only forwards the
structured alarm documents the diagnose pass already produced.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import ALARM_PREFIX, WEBHOOK_TIMEOUT_SEC, WEBHOOK_URL

SCHEMA = "self_aware_webhook_outbox_v1"


def _post_events(url: str, events: list[dict[str, Any]], timeout: float = WEBHOOK_TIMEOUT_SEC) -> dict[str, Any]:
    """POST one structured JSON alert to ``url``. Best-effort; never raises."""
    payload = json.dumps({"schema": SCHEMA, "source": "self_aware", "events": events},
                         sort_keys=True, default=str).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return {"sent": len(events), "status_code": response.status,
                    "elapsed_sec": round(time.time() - started, 3), "error": None}
    except urllib.error.HTTPError as exc:
        return {"sent": 0, "status_code": exc.code, "elapsed_sec": round(time.time() - started, 3),
                "error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"sent": 0, "status_code": None, "elapsed_sec": round(time.time() - started, 3),
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def _fsync_parent(path: Path) -> None:
    try:
        descriptor = os.open(str(path.parent), os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
    finally:
        try:
            os.close(descriptor)  # noqa: B012 - descriptor is bound above
        except Exception:  # noqa: BLE001
            pass


class WebhookAlertOutbox:
    """Durable pending queue for alerts that have not yet been acked by a sink.

    Persisted as one JSONL file (append + fsync). ``enqueue`` is durable before
    delivery; ``ack`` marks delivered events. Bounded retention keeps the file
    from growing without limit if a sink is down for a long time.
    """

    MAX_PENDING = 2000

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._pending: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                eid = row.get("event_id")
                if eid:
                    self._pending[eid] = row
        except (OSError, ValueError):
            # A corrupt outbox must never block alarm delivery; preserve bytes.
            quarantine = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time() * 1000)}")
            try:
                os.replace(self.path, quarantine)
            except OSError:
                pass

    def _append(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent(self.path)

    def enqueue(self, events: list[dict[str, Any]]) -> int:
        rows = []
        for e in events:
            eid = str(e.get("event_id") or e.get("event") or "") or (
                f"{e.get('check')}:{e.get('at')}:{time.time_ns()}")
            if eid in self._pending:
                continue
            row = {**e, "event_id": eid, "created_at_unix": time.time(), "attempts": 0, "acked": False}
            rows.append(row)
            self._pending[eid] = row
        if rows:
            self._append(rows)
        return len(rows)

    def due(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = [r for r in self._pending.values() if not r.get("acked")]
        rows.sort(key=lambda r: r.get("created_at_unix") or 0)
        return rows[:limit]

    def ack(self, event_ids: list[str]) -> None:
        changed = False
        for eid in event_ids:
            row = self._pending.get(eid)
            if row and not row.get("acked"):
                row["acked"] = True
                row["acked_at_unix"] = time.time()
                changed = True
        if changed:
            # Rewrite the whole (bounded) file to drop acked rows.
            self._compact()

    def _compact(self) -> None:
        keep = [r for r in self._pending.values() if not r.get("acked")][-self.MAX_PENDING:]
        tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False,
                                          dir=str(self.path.parent), prefix=f".{self.path.name}.",
                                          suffix=".tmp")
        try:
            for row in keep:
                tmp.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, self.path)
            _fsync_parent(self.path)
        finally:
            try:
                os.unlink(tmp.name)
            except (OSError, FileNotFoundError):
                pass


def flush_webhook(events: list[dict[str, Any]], *, outbox_path: str | os.PathLike[str],
                  url: str = WEBHOOK_URL, timeout: float = WEBHOOK_TIMEOUT_SEC) -> dict[str, Any]:
    """Enqueue + attempt delivery of ``selfaware.*`` alerts to the webhook sink.

    Returns a summary; never raises. When ``url`` is empty the outbox is still
    updated (so a later enabling of the URL can flush), but nothing is posted.
    """
    if not url:
        return {"enabled": False, "sent": 0, "pending": 0, "note": "SELF_AWARE_WEBHOOK_URL unset"}
    outbox = WebhookAlertOutbox(outbox_path)
    outbox.enqueue(events)
    due = outbox.due()
    if not due:
        return {"enabled": True, "sent": 0, "pending": 0}
    result = _post_events(url, due, timeout=timeout)
    if result.get("sent"):
        outbox.ack([r["event_id"] for r in due])
    return {"enabled": True, **result, "pending": len(outbox.due())}
