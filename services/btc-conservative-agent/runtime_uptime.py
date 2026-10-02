"""Uninterrupted-runtime tracker shared by the Fly bot and the analyzer dashboard.

An *interruption* is one contiguous period in which the paper runtime is not
running unattended: a process restart or deploy, an execution pause (any owner,
including DEPLOY_MAINTENANCE), paper switched off (no registry tile ON), or the
AI cadence stalled beyond the health threshold. A maintenance pause that ends in
a restart on a new revision is one interruption (the guarded deploy), not two.

State is durable (one small JSON file on the bot's data volume) so a restart is
recorded as an interruption with the downtime measured from the last persisted
heartbeat. All durations are computed from server clocks, never browser time.
Read-only with respect to trading: nothing here touches orders, relay or Bitfinex.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = "runtime_uptime_v1"
STATE_FILE = "runtime_uptime_v1.json"
AEST = timezone(timedelta(hours=10), "AEST")
HISTORY_SEC = 7 * 24 * 3600
MAX_HISTORY = 500
PERSIST_EVERY_SEC = 60.0
# Matches the Fly self-check fly.ai_success RED threshold.
AI_STALL_SEC = 720.0
AI_STALL_FAILURES = 3
# A restart this long after the previous heartbeat is not a continuation of the
# interruption that was open when the old process stopped.
CONTINUE_OPEN_INTERRUPTION_SEC = 2 * 3600
DEPLOY_PAUSE_OWNER = "DEPLOY_MAINTENANCE"
PROOF_WINDOW_HOURS = 48.0
DEFINITION = ("Time since the last interruption: process restart/deploy, execution pause (any owner), "
              "paper off (no registry tile ON), or AI cadence stall (no successful AI response for "
              f"{int(AI_STALL_SEC // 60)} min or {AI_STALL_FAILURES} consecutive failures). A restart resets it.")


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def clock(ts: float | None, now: float | None = None) -> dict:
    """``{"aest": "17:39 AEST", "utc": "07:39 UTC"}``; the date is added when not today (AEST)."""
    if ts is None:
        return {"aest": None, "utc": None}
    moment = datetime.fromtimestamp(float(ts), timezone.utc)
    today = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).astimezone(AEST).date()
    local = moment.astimezone(AEST)
    fmt = "%H:%M" if local.date() == today else "%d %b %H:%M"
    return {"aest": local.strftime(fmt) + " AEST", "utc": moment.strftime(fmt) + " UTC"}


def duration(sec: float | None) -> str:
    if sec is None:
        return "unknown"
    sec = max(0, int(sec))
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


def _short_rev(rev) -> str:
    rev = str(rev or "").strip()
    return rev[:7] if rev and rev != "unknown" else "unknown revision"


def problem_from(*, paused: bool, pause_owner=None, pause_reason=None, tiles_on: int | None = None,
                 tiles_total: int | None = None, ai_success_age_sec: float | None = None,
                 ai_consecutive_failures: int = 0, process_age_sec: float | None = None) -> dict | None:
    """The first reason the runtime is not running unattended right now, or None."""
    if paused:
        owner = str(pause_owner or "unattributed")
        detail = f"paused by {owner}" + (f" ({pause_reason})" if pause_reason and pause_reason != owner else "")
        return {"kind": "deploy_pause" if owner == DEPLOY_PAUSE_OWNER else "pause", "owner": owner,
                "text": detail[:200]}
    if tiles_total is not None and tiles_total > 0 and (tiles_on or 0) == 0:
        return {"kind": "paper_off", "owner": None, "text": f"paper off (0/{tiles_total} tiles ON)"}
    failures = int(ai_consecutive_failures or 0)
    if ai_success_age_sec is None:
        stalled = process_age_sec is not None and process_age_sec > AI_STALL_SEC
        age_text = "no successful AI response since boot"
    else:
        stalled = ai_success_age_sec > AI_STALL_SEC
        age_text = f"last successful AI response {int(ai_success_age_sec // 60)}m ago"
    if stalled or failures >= AI_STALL_FAILURES:
        return {"kind": "ai_stall", "owner": None, "text": f"AI cadence stalled: {age_text}, {failures} consecutive failures"}
    return None


class UptimeTracker:
    """Thread-safe, durable uninterrupted-runtime state machine."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.state: dict = {}
        self._persisted_at = 0.0
        self.persist_error: str | None = None

    # ------------------------------------------------------------ persistence
    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) and raw.get("schema") == SCHEMA else {}

    def _persist(self, now: float) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".runtime-uptime-", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self.state, handle, separators=(",", ":"))
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._persisted_at = now
            self.persist_error = None
        except Exception as exc:  # never break the heartbeat over telemetry
            self.persist_error = f"{type(exc).__name__}: {exc}"[:200]

    def _trim(self, now: float) -> None:
        horizon = now - HISTORY_SEC
        self.state["interruptions"] = [
            i for i in self.state.get("interruptions") or []
            if i.get("ended_at") is None or float(i["ended_at"]) >= horizon][-MAX_HISTORY:]
        self.state["runs"] = [r for r in self.state.get("runs") or [] if float(r["end"]) >= horizon][-MAX_HISTORY:]

    def _close_run(self, end: float) -> None:
        start = self.state.get("run_started_at")
        if start is not None and end > float(start):
            self.state.setdefault("runs", []).append({"start": float(start), "end": float(end)})
        self.state["run_started_at"] = None

    def _open(self, now: float, problem: dict) -> None:
        entry = {"kind": problem["kind"], "owner": problem.get("owner"), "at": now, "ended_at": None,
                 "text": problem["text"], "revision": self.state.get("revision")}
        self.state.setdefault("interruptions", []).append(entry)
        self.state["current"] = dict(entry)
        self.state["blocker"] = dict(problem)

    # ------------------------------------------------------------ transitions
    def boot(self, now: float, revision: str) -> dict:
        """Record this process start; returns the interruption it opened or continued."""
        with self.lock:
            prev = self._load()
            self.state = prev or {"schema": SCHEMA, "interruptions": [], "runs": []}
            last_seen = _parse(prev.get("heartbeat_at")) if prev else None
            prev_rev = prev.get("revision") if prev else None
            if prev:
                self._close_run(last_seen if last_seen is not None else now)
            deploy = bool(prev_rev) and prev_rev != revision
            kind = "deploy" if deploy else ("restart" if prev else "first_boot")
            label = {"deploy": f"deploy {_short_rev(revision)}",
                     "restart": f"process restart (same revision {_short_rev(revision)})",
                     "first_boot": "first recorded boot"}[kind]
            down = None if last_seen is None else max(0.0, now - last_seen)
            open_prev = (prev.get("current") if prev else None) or None
            self.state.update(schema=SCHEMA, revision=revision, boot_at=now, heartbeat_at=now, run_started_at=None)
            if (open_prev and last_seen is not None and down is not None
                    and down <= CONTINUE_OPEN_INTERRUPTION_SEC):
                started = clock(open_prev.get("at"), now)["aest"]
                text = (f"{label} at {clock(now, now)['aest']} after {open_prev.get('text')} from {started}"
                        if deploy and open_prev.get("kind") == "deploy_pause"
                        else f"{open_prev.get('text')}, then {label} at {clock(now, now)['aest']}")
                for entry in reversed(self.state.get("interruptions") or []):
                    if entry.get("ended_at") is None and entry.get("at") == open_prev.get("at"):
                        entry.update(kind=kind if deploy else entry.get("kind"), text=text[:240], revision=revision,
                                     down_sec=down)
                        self.state["current"] = dict(entry)
                        break
                else:
                    self._open(now, {"kind": kind, "text": text[:240]})
            else:
                text = f"{label} at {clock(now, now)['aest']}" + (
                    f" (down {duration(down)})" if down is not None and down >= 60 else "")
                self._open(now, {"kind": kind, "owner": None, "text": text})
                self.state["current"]["down_sec"] = down
                self.state["interruptions"][-1]["down_sec"] = down
            self.state["blocker"] = {"kind": "booting", "owner": None, "text": "process starting"}
            self._trim(now)
            self._persist(now)
            return dict(self.state["current"])

    def observe(self, now: float, problem: dict | None) -> None:
        """Apply one health observation (``problem_from`` output)."""
        with self.lock:
            if not self.state:
                return
            changed = False
            current = self.state.get("current")
            if problem and not current:
                self._close_run(now)
                self._open(now, problem)
                changed = True
            elif problem and current:
                if (self.state.get("blocker") or {}).get("kind") != problem["kind"]:
                    changed = True
                self.state["blocker"] = dict(problem)
            elif not problem and current:
                for entry in reversed(self.state.get("interruptions") or []):
                    if entry.get("ended_at") is None:
                        entry["ended_at"] = now
                        break
                closed = dict(current, ended_at=now)
                self.state["last_interruption"] = closed
                self.state["current"] = None
                self.state["blocker"] = None
                self.state["run_started_at"] = now
                changed = True
            self.state["heartbeat_at"] = now
            if changed:
                self._trim(now)
            if changed or now - self._persisted_at >= PERSIST_EVERY_SEC:
                self._persist(now)

    # ------------------------------------------------------------ read model
    def summary(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.lock:
            st = json.loads(json.dumps(self.state)) if self.state else {}
        if not st:
            return {"schema": SCHEMA, "available": False, "reason": "uptime tracker not started",
                    "definition": DEFINITION}
        current, blocker = st.get("current"), st.get("blocker") or {}
        run_start = st.get("run_started_at")
        running = current is None and run_start is not None
        if running:
            state, colour = "RUNNING", "green"
        elif blocker.get("kind") == "deploy_pause" or (
                blocker.get("kind") == "booting" and (current or {}).get("kind") == "deploy"):
            state, colour = "PAUSED_DEPLOY", "amber"
        else:
            state, colour = "INTERRUPTED", "red"
        last = st.get("last_interruption") if running else current
        interruptions = st.get("interruptions") or []
        count_24h = sum(1 for i in interruptions if float(i.get("at") or 0) >= now - 86400)
        horizon = now - HISTORY_SEC
        spans = [(max(float(r["start"]), horizon), float(r["end"])) for r in st.get("runs") or []]
        if running:
            spans.append((max(float(run_start), horizon), now))
        longest = max((b - a for a, b in spans if b > a), default=0.0)
        uninterrupted = (now - float(run_start)) if running else None
        since_ts = float(run_start) if running else float((current or {}).get("at") or now)
        since = clock(since_ts, now)
        cause = (last or {}).get("text")
        if running:
            label = f"Running uninterrupted: {duration(uninterrupted)}"
        else:
            label = (f"Interrupted {duration(now - since_ts)}: {blocker.get('text') or cause or 'unknown'}")
        out = {
            "schema": SCHEMA, "available": True, "generated_at": _iso(now),
            "state": state, "colour": colour, "running": running,
            "uninterrupted_sec": None if uninterrupted is None else int(uninterrupted),
            "uninterrupted_label": label,
            "since": _iso(since_ts), "since_aest": since["aest"], "since_utc": since["utc"],
            "last_interruption": None if not last else {
                "kind": last.get("kind"), "at": _iso(last.get("at")), "ended_at": _iso(last.get("ended_at")),
                "text": cause, "down_sec": last.get("down_sec"), "revision": last.get("revision")},
            "current_blocker": None if running else {"kind": blocker.get("kind"), "text": blocker.get("text"),
                                                     "owner": blocker.get("owner")},
            "interruptions_24h": count_24h,
            "longest_run_7d_sec": int(longest), "longest_run_7d_label": duration(longest),
            "boot_at": _iso(st.get("boot_at")), "revision": st.get("revision"),
            "definition": DEFINITION,
            "source": "durable runtime state (" + STATE_FILE + " on the bot data volume)",
        }
        if self.persist_error:
            out["persist_error"] = self.persist_error
        return out


def proof_progress(active, now: float | None = None) -> dict | None:
    """48h unattended-proof progress from ``laptop-chain/unattended-proof/active.json``."""
    if not isinstance(active, dict):
        return None
    now = time.time() if now is None else now
    t0, ends = _parse(active.get("t0")), _parse(active.get("ends_at"))
    if t0 is None or ends is None or ends <= t0:
        return None
    window_h = round((ends - t0) / 3600.0, 1)
    verdict = active.get("verdict") if isinstance(active.get("verdict"), dict) else None
    status = verdict or (active.get("status") if isinstance(active.get("status"), dict) else {}) or {}
    elapsed_h = max(0.0, min(now, ends) - t0) / 3600.0
    result = str(status.get("result") or ("COMPLETE" if now >= ends else "IN_PROGRESS"))
    label = f"Proof: {int(elapsed_h)}h / {int(window_h)}h"
    if result not in ("IN_PROGRESS",):
        label += f" ({result})"
    return {"t0": _iso(t0), "ends_at": _iso(ends), "elapsed_hours": round(elapsed_h, 2),
            "window_hours": window_h, "result": result, "label": label,
            "reasons": [str(r)[:200] for r in (status.get("reasons") or [])[:3]]}


def read_proof_progress(state_dir, now: float | None = None) -> dict | None:
    try:
        path = Path(state_dir) / "unattended-proof" / "active.json"
        return proof_progress(json.loads(path.read_text(encoding="utf-8-sig")), now)
    except (OSError, ValueError):
        return None


def sanitize_proof(value) -> dict | None:
    """Bound a proof summary that arrived over the network (laptop -> Fly push)."""
    if not isinstance(value, dict):
        return None
    try:
        return {"t0": str(value.get("t0"))[:40], "ends_at": str(value.get("ends_at"))[:40],
                "elapsed_hours": float(value.get("elapsed_hours")), "window_hours": float(value.get("window_hours")),
                "result": str(value.get("result"))[:40], "label": str(value.get("label"))[:80],
                "reasons": [str(r)[:200] for r in (value.get("reasons") or [])[:3]]}
    except (TypeError, ValueError):
        return None


_FLY_CACHE = {"at": 0.0, "value": None, "error": None}
_FLY_CACHE_LOCK = threading.Lock()


def fetch_fly_uptime(url: str, *, timeout: float = 5.0, max_age: float = 30.0) -> dict:
    """Fly's uptime block from ``/api/status`` for the analyzer (cached; never raises)."""
    now = time.time()
    with _FLY_CACHE_LOCK:
        if now - _FLY_CACHE["at"] < max_age:
            return {"uptime": _FLY_CACHE["value"], "error": _FLY_CACHE["error"]}
    value, error = None, None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            payload = json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))
        value = payload.get("uptime") if isinstance(payload, dict) else None
        if not isinstance(value, dict):
            value, error = None, "Fly /api/status has no uptime block yet (ships with the next guarded deploy)"
    except Exception as exc:
        error = f"Fly /api/status unreachable: {type(exc).__name__}"
    with _FLY_CACHE_LOCK:
        _FLY_CACHE.update(at=now, value=value, error=error)
    return {"uptime": value, "error": error}
