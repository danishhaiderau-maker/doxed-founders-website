"""Pure helpers for the API-GAP-SWEEP-20261012 monitor routes (R1-R4, A1-A8).

Nothing here touches trading, relay, exchange or AI state.  bot.py hands in
plain snapshots and these functions shape them and decide alert levels.  All
functions are deterministic, JSON-safe and never raise on malformed input.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from typing import Any, Iterable, Mapping, Optional

WORKERS_SCHEMA = "monitor_workers_v1"
FILL_SIM_SCHEMA = "monitor_fill_sim_v1"
PROTECTIVE_SCHEMA = "monitor_protective_coverage_v1"
SWITCHES_SCHEMA = "monitor_tile_switches_v1"
CONFIG_SCHEMA = "monitor_effective_config_v1"
PREENTRY_SCHEMA = "monitor_preentry_v1"
QUEUES_SCHEMA = "monitor_exit_queues_v1"
COUNTERS_SCHEMA = "monitor_persisted_counters_v1"
WARMUP_SCHEMA = "monitor_warmup_v1"
REGIME_SCHEMA = "monitor_regime_v1"
SHIPPER_SCHEMA = "monitor_shipper_custody_v1"

RED, AMBER, GREEN = "RED", "AMBER", "GREEN"

# ----------------------------------------------------------------------------
# R1 workers
# ----------------------------------------------------------------------------
# Every long-running worker started by main() plus the ones started without
# safe_thread.  (expected_interval_s, min_stale_s).  "oneshot" workers run once.
WORKER_EXPECTATIONS: dict[str, tuple] = {
    "start_websocket": (5.0, 120.0),
    "ws_tick_lifecycle_worker": (0.5, 30.0),
    "protective_exit_worker": (0.25, 30.0),
    "state_monitor_loop": (1.0, 30.0),
    "bbo_refresh_loop": (1.0, 60.0),
    "order_book_refresh_loop": (1.0, 60.0),
    "ohlcv_refresh_loop": (5.0, 120.0),
    "microstructure_capture_loop": (1.0, 60.0),
    "xvl_evaluator_thread": (1.0, 60.0),
    "gf3_paper_thread": (1.0, 60.0),
    "gf3l_paper_thread": (1.0, 60.0),
    "_platform_health_loop": (180.0, 600.0),
    "ai_shadow_maturation_loop": (5.0, 120.0),
    "engine_loop": (1.0, 120.0),
    "tick_execution_engine": (0.25, 30.0),
    "ws_watchdog": (3.0, 60.0),
    "analytics_loop": (60.0, 0.0),
    # R17: all_opportunity_future_path_evidence_loop is retired — not started in
    # main() (disabled state only). Keep it out of expectations so the workers
    # monitor does not RED on NOT_STARTED.
    "position_manager": (1.0, 60.0),
    "ttl_monitor": (30.0, 300.0),
    "periodic_pipeline_loop": (30.0, 300.0),
    "watchdog_loop": (5.0, 120.0),
    "dashboard_http_watchdog_loop": (30.0, 120.0),
    "bitfinex_live_reconcile_loop": (30.0, 300.0),
    "_platform_relay_connection_keepalive_loop": (30.0, 600.0),
    "_live_copy_delivery_loop": (15.0, 120.0),
    "heartbeat_loop": (30.0, 300.0),
    "runtime_uptime_loop": (60.0, 600.0),
    "live_guardian_loop": (1.0, 30.0),   # #784: beats via shutdown_event.wait(1 s)
    "api_state_cache_refresher": (5.0, 900.0),
    "main_supervisor_loop": (60.0, 0.0),
    "r5_retention_loop": (600.0, 1800.0),  # R5 disk-cap pass every 10 min
}
ONESHOT_WORKERS = ("retired_tile_boundary_worker", "regime_bars_boot_hydrate")


def worker_rows(threads: Mapping[str, Mapping], oneshots: Mapping[str, Mapping] | None = None,
                *, expected: Iterable[str] = (), now: float | None = None) -> dict:
    """Shape ``thread_health_v1`` snapshot rows into the /api/monitor/workers payload."""
    now = time.time() if now is None else float(now)
    rows, red, amber = [], [], []
    names = sorted(set(threads or {}) | set(expected or ()))
    for name in names:
        row = dict((threads or {}).get(name) or {})
        last_ts = row.get("last_tick_ts")
        entry = {
            "name": name,
            "kind": "loop",
            "monitored": bool(row.get("monitored")),
            "started": bool(row.get("started")),
            "alive": row.get("alive"),
            "last_progress_ts": last_ts,
            "last_progress_age_s": row.get("last_tick_age_sec"),
            "iterations": int(row.get("iterations") or 0),
            "restarts": int(row.get("restarts") or 0),
            "last_restart_reason": row.get("last_restart_reason"),
            "last_restart_age_s": row.get("last_restart_age_sec"),
            "errors": int(row.get("error_count") or 0),
            "last_error": row.get("last_error"),
            "expected_interval_s": row.get("interval_sec"),
            "stale_after_s": row.get("stale_after_sec"),
            "stale": bool(row.get("stale")),
        }
        reasons = []
        if not row:
            reasons.append("NOT_REGISTERED")
        elif not entry["monitored"]:
            reasons.append("UNMONITORED")
        elif not entry["started"]:
            reasons.append("NOT_STARTED")
        if entry["stale"]:
            reasons.append("STALE")
        if entry["alive"] is False:
            reasons.append("DEAD")
        restart_age = entry["last_restart_age_s"]
        if entry["restarts"] and restart_age is not None and restart_age <= 900:
            reasons.append("RESTARTED_15M")
        entry["alerts"] = reasons
        entry["level"] = RED if reasons else GREEN
        if reasons:
            red.append(name)
        rows.append(entry)
    for name, info in sorted((oneshots or {}).items()):
        info = dict(info or {})
        state = str(info.get("state") or "UNKNOWN").upper()
        level = RED if state in ("FAILED", "UNKNOWN") else (AMBER if state in ("RUNNING", "PENDING") else GREEN)
        entry = {"name": name, "kind": "oneshot", "monitored": True, "state": state,
                 "error": info.get("error"), "finished_at": info.get("finished_at"),
                 "alerts": [f"ONESHOT_{state}"] if level != GREEN else [], "level": level}
        (red if level == RED else amber if level == AMBER else []).append(name)
        rows.append(entry)
    return {
        "schema": WORKERS_SCHEMA,
        "count": len(rows),
        "monitored": sum(1 for r in rows if r.get("monitored")),
        "red": sorted(red),
        "amber": sorted(amber),
        "level": RED if red else (AMBER if amber else GREEN),
        "workers": rows,
    }


# ----------------------------------------------------------------------------
# R2 paper fill-sim
# ----------------------------------------------------------------------------
class FillSimCounters:
    """Per-lane counters for the paper maker/chase fill simulation (never raises)."""

    def __init__(self, clock=time.time, window_s: float = 300.0):
        self._clock = clock
        self._lock = threading.Lock()
        self._window = float(window_s)
        self.polls = 0
        self.errors = 0
        self.last_error: Optional[str] = None
        self.last_error_ts = 0.0
        self._error_ts: deque = deque(maxlen=1000)
        self._lanes: dict[str, dict] = {}

    def poll(self) -> None:
        with self._lock:
            self.polls += 1

    def error(self, exc, where: str = "touch_grid") -> None:
        try:
            now = float(self._clock())
            text = f"{where}: {type(exc).__name__}: {exc}"[:240] if isinstance(exc, BaseException) else str(exc)[:240]
            with self._lock:
                self.errors += 1
                self.last_error = text
                self.last_error_ts = now
                self._error_ts.append(now)
        except Exception:
            pass

    def maker_fill(self, lane: str, fill_price, bid, ask, side: str = "") -> None:
        try:
            fill, bid, ask = float(fill_price), float(bid or 0), float(ask or 0)
            outside = None
            if bid > 0 and ask > 0 and fill > 0:
                mid = (bid + ask) / 2.0
                if fill > ask:
                    outside = (fill - ask) / mid * 1e4
                elif fill < bid:
                    outside = (bid - fill) / mid * 1e4
                else:
                    outside = 0.0
            with self._lock:
                row = self._lanes.setdefault(str(lane or "UNKNOWN"), {"maker_fills": 0, "outside_bp": deque(maxlen=500),
                                                                     "outside_gt_1bp": 0, "no_bbo": 0})
                row["maker_fills"] += 1
                if outside is None:
                    row["no_bbo"] += 1
                else:
                    row["outside_bp"].append(outside)
                    if outside > 1.0:
                        row["outside_gt_1bp"] += 1
        except Exception:
            pass

    def taker_fill(self, lane: str, fill_price, bid, ask, side: str = "") -> None:
        """Book-walked taker fills (every paper tile) are counted apart from resting maker fills: a depth walk at
        signal+delay legitimately prints outside the CURRENT best bid/ask, so it must never feed FILL_OUTSIDE_BBO."""
        try:
            with self._lock:
                row = self._lanes.setdefault(str(lane or "UNKNOWN"), {"maker_fills": 0, "outside_bp": deque(maxlen=500),
                                                                     "outside_gt_1bp": 0, "no_bbo": 0})
                row["taker_book_walk_fills"] = row.get("taker_book_walk_fills", 0) + 1
        except Exception:
            pass

    def snapshot(self) -> dict:
        now = float(self._clock())
        with self._lock:
            recent = sum(1 for ts in self._error_ts if now - ts <= self._window)
            lanes = {}
            for lane, row in self._lanes.items():
                vals = sorted(row["outside_bp"])
                lanes[lane] = {"maker_fills": row["maker_fills"], "no_bbo": row["no_bbo"],
                               "taker_book_walk_fills": row.get("taker_book_walk_fills", 0),
                               "outside_bbo_gt_1bp": row["outside_gt_1bp"],
                               "fill_vs_bbo_bp_p50": _q(vals, 0.5), "fill_vs_bbo_bp_p95": _q(vals, 0.95)}
            payload = {"schema": FILL_SIM_SCHEMA, "touch_grid_polls": self.polls, "errors": self.errors,
                       "errors_5m": recent, "last_error": self.last_error,
                       "last_error_age_s": round(now - self.last_error_ts, 1) if self.last_error_ts else None,
                       "lanes": lanes}
        alerts = []
        if recent:
            alerts.append("FILL_SIM_ERRORS_5M")
        if any(v["outside_bbo_gt_1bp"] for v in lanes.values()):
            alerts.append("FILL_OUTSIDE_BBO_GT_1BP")
        payload["alerts"] = alerts
        payload["level"] = RED if "FILL_SIM_ERRORS_5M" in alerts else (AMBER if alerts else GREEN)
        return payload


def _q(values: list, q: float):
    if not values:
        return None
    idx = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
    return round(float(values[idx]), 3)


# ----------------------------------------------------------------------------
# R3 protective coverage
# ----------------------------------------------------------------------------
def protective_coverage(rows: Iterable[Mapping], policy_errors: int = 0) -> dict:
    """rows: {trade_id, lane, covered(bool), source, reason}."""
    rows = [dict(r) for r in rows or ()]
    uncovered = [{k: r.get(k) for k in ("trade_id", "lane", "reason")} for r in rows if not r.get("covered")]
    fallback = [{k: r.get(k) for k in ("trade_id", "lane", "reason")} for r in rows
                if r.get("covered") and r.get("source") == "FALLBACK"]
    alerts = []
    if uncovered:
        alerts.append("OPEN_POSITION_WITHOUT_STOP")
    if fallback:
        alerts.append("FALLBACK_STOP_IN_USE")
    return {"schema": PROTECTIVE_SCHEMA, "open_positions": len(rows), "covered": len(rows) - len(uncovered),
            "uncovered_count": len(uncovered), "uncovered": uncovered, "fallback": fallback,
            "policy_errors": int(policy_errors or 0), "alerts": alerts,
            "level": RED if uncovered else (AMBER if fallback or policy_errors else GREEN)}


# ----------------------------------------------------------------------------
# R4 tile switches
# ----------------------------------------------------------------------------
def tile_switches(tiles: Iterable[Mapping], build_text: str = "") -> dict:
    """tiles rows: lane, number, label, registry_state, retired, paper_on, live_orders_on,
    relay_eligible, evaluator_running, evaluations_since_off, rows_since_off, active_listed."""
    out, alerts = [], []
    for t in tiles or ():
        t = dict(t)
        reasons = []
        if t.get("retired") and t.get("active_listed"):
            reasons.append("RETIRED_BUT_ACTIVE_LISTED")
        if t.get("retired") and int(t.get("evaluations_since_off") or 0) > 0:
            reasons.append("RETIRED_STILL_EVALUATED")
        if (t.get("retired") or not t.get("paper_on")) and int(t.get("rows_since_off") or 0) > 0:
            reasons.append("OFF_TILE_PRODUCING_ROWS")
        if t.get("live_orders_on") and (t.get("retired") or not t.get("relay_eligible")):
            reasons.append("LIVE_ON_BUT_NOT_ELIGIBLE")
        if t.get("retired") and t.get("paper_on"):
            reasons.append("RETIRED_BUT_PAPER_ON")
        t["disagreements"] = reasons
        alerts.extend(f"{t.get('lane')}:{r}" for r in reasons)
        out.append(t)
    on = [t for t in out if t.get("paper_on") and not t.get("retired")]
    live = [t for t in out if t.get("live_orders_on") and not t.get("retired")]
    return {"schema": SWITCHES_SCHEMA, "tiles": out, "paper_on_count": len(on), "live_on_count": len(live),
            "build_features": build_text, "alerts": alerts, "level": RED if alerts else GREEN}


def live_arming_word(live_state: Optional[Mapping]) -> str:
    """ARMED / DISARMED only from a clean boolean effective_live; anything doubtful reads 'unknown'.

    Unknown = no state, an ``error`` key, WEBSITE_UNREACHABLE (or MONITOR_NOT_WIRED) among the blockers, or a
    non-boolean effective_live. Never DISARMED just because the state could not be read."""
    if not isinstance(live_state, Mapping) or live_state.get("error"):
        return "live arming state unknown"
    blockers = {str(b).upper() for b in (live_state.get("blockers") or ())}
    if blockers & {"WEBSITE_UNREACHABLE", "MONITOR_NOT_WIRED"}:
        return "live arming state unknown"
    eff = live_state.get("effective_live")
    if eff is True:
        return "live copy ARMED"
    if eff is False:
        return "live copy DISARMED" + (" (force_paper)" if live_state.get("force_paper_mode") else "")
    return "live arming state unknown"


def build_features_text(tiles: Iterable[Mapping], live_state: Optional[Mapping] = None) -> str:
    """Current-state build text derived from the one switch source (replaces the stale prose).

    ``live_orders_on`` on a tile row means live-ELIGIBLE (the switch), not that live is armed, so the text says
    "live-eligible" and states the real arming separately (live_state = live_flags_snapshot()):
    effective_live false -> DISARMED, true -> ARMED, missing -> state unknown. Never "live copy ON" for a switch."""
    tiles = [t for t in tiles or () if not t.get("retired")]
    paper = [t for t in tiles if t.get("paper_on")]
    live = [t for t in tiles if t.get("live_orders_on")]
    def names(rows):
        return ", ".join(f"T{t.get('number')} {t.get('label')}" for t in rows) or "none"
    arming = live_arming_word(live_state)
    return (f"{len(tiles)} registered tiles; paper ON: {len(paper)} ({names(paper)}); "
            f"live-eligible switch ON: {len(live)} ({names(live)}); {arming}. Derived at request time from the "
            f"tile switch source (/api/monitor/tiles/switches); retired tiles are excluded.")


# ----------------------------------------------------------------------------
# A1 effective config
# ----------------------------------------------------------------------------
_SECRET_RE = re.compile(r"(SECRET|TOKEN|KEY|PASS|PASSWORD|PRIVATE|CREDENTIAL|AUTH|COOKIE|SIGNATURE|HMAC|DSN|WEBHOOK|SALT)",
                        re.I)
_URL_CRED_RE = re.compile(r"://[^/@\s]+@")


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_RE.search(str(key or "")))


def redact(key: str, value) -> Any:
    if value is None:
        return None
    text = str(value)
    if is_secret_key(key):
        return {"redacted": True, "set": bool(text), "len": len(text),
                "sha8": hashlib.sha256(text.encode()).hexdigest()[:8] if text else None}
    if _URL_CRED_RE.search(text):
        return _URL_CRED_RE.sub("://***@", text)
    return text[:512]


def effective_config(keys: Iterable[str], env: Mapping[str, str] | None = None,
                     defaults: Mapping[str, Any] | None = None, expected: Mapping[str, Any] | None = None) -> dict:
    env = os.environ if env is None else env
    defaults = defaults or {}
    expected = expected or {}
    rows, h = [], hashlib.sha256()
    mismatches = []
    for key in sorted(set(keys or ())):
        raw = env.get(key)
        source = "env" if raw is not None else ("default" if key in defaults else "unset")
        eff = raw if raw is not None else defaults.get(key)
        h.update(f"{key}={'' if eff is None else eff}\n".encode())
        row = {"key": key, "effective": redact(key, eff), "default": redact(key, defaults.get(key)),
               "source": source, "secret": is_secret_key(key),
               "sha": hashlib.sha256(str(eff).encode()).hexdigest()[:12] if eff is not None else None}
        if key in expected and str(expected[key]) != str(eff):
            row["expected_mismatch"] = True
            mismatches.append(key)
        rows.append(row)
    return {"schema": CONFIG_SCHEMA, "count": len(rows), "config_hash": h.hexdigest()[:16],
            "expected_mismatches": mismatches, "keys": rows,
            "level": AMBER if mismatches else GREEN}


def env_keys_in_source(source_text: str) -> list[str]:
    pat = re.compile(r"""os\.(?:getenv|environ\.get)\(\s*["']([A-Z0-9_]+)["']|os\.environ\[\s*["']([A-Z0-9_]+)["']\s*\]""")
    keys = set()
    for m in pat.finditer(source_text or ""):
        keys.add(m.group(1) or m.group(2))
    return sorted(keys)


# ----------------------------------------------------------------------------
# A2 pre-entry
# ----------------------------------------------------------------------------
class DegradedTimer:
    def __init__(self, clock=time.time):
        self._clock = clock
        self.since = None

    def observe(self, degraded: bool) -> Optional[float]:
        now = float(self._clock())
        if degraded:
            if self.since is None:
                self.since = now
            return round(now - self.since, 1)
        self.since = None
        return None


def preentry_level(health: str, degraded_for_s, oldest_pending_age_s, triggers_recent: int) -> tuple[str, list]:
    alerts = []
    if str(health).upper() == "DEGRADED" and degraded_for_s is not None and degraded_for_s > 120:
        alerts.append("PREENTRY_DEGRADED_GT_2M")
    try:
        if oldest_pending_age_s is not None and float(oldest_pending_age_s) > 60 and int(triggers_recent or 0) > 0:
            alerts.append("PREENTRY_PENDING_GT_60S_WITH_TRIGGERS")
    except (TypeError, ValueError):
        pass
    return (RED if alerts else (AMBER if str(health).upper() == "DEGRADED" else GREEN)), alerts


# ----------------------------------------------------------------------------
# A3 tick drops while open
# ----------------------------------------------------------------------------
class DropRate:
    # A single dropped exit tick while a paper position is open is common WS jitter and was
    # flapping fly.gap.exit_queues AMBER (watcher.flapping). Require a short burst before alerting.
    ALERT_MIN_DROPS_WHILE_OPEN = 3

    def __init__(self, clock=time.time, window_s: float = 60.0,
                 alert_min_drops: int = ALERT_MIN_DROPS_WHILE_OPEN):
        self._clock = clock
        self._window = window_s
        self._alert_min = max(1, int(alert_min_drops))
        self._lock = threading.Lock()
        self._events: deque = deque(maxlen=10000)
        self.total = 0
        self.while_open = 0

    def drop(self, positions_open: bool) -> None:
        now = float(self._clock())
        with self._lock:
            self.total += 1
            if positions_open:
                self.while_open += 1
            self._events.append((now, bool(positions_open)))

    def snapshot(self) -> dict:
        now = float(self._clock())
        with self._lock:
            recent = [e for e in self._events if now - e[0] <= self._window]
        per_min_open = sum(1 for _, o in recent if o)
        alerts = ["EXIT_TICK_DROPS_WHILE_OPEN"] if per_min_open >= self._alert_min else []
        return {"dropped_total": self.total, "dropped_while_open": self.while_open,
                "drops_last_min": len(recent), "drops_last_min_while_open": per_min_open,
                "alert_min_drops_while_open": self._alert_min,
                "alerts": alerts, "level": AMBER if alerts else GREEN}


# ----------------------------------------------------------------------------
# A4 persisted rolling counters
# ----------------------------------------------------------------------------
class PersistedWindows:
    """Rolling 1 h / 24 h event counters that survive restarts (JSON file, atomic replace)."""

    WINDOWS = {"1h": 3600.0, "24h": 86400.0}

    def __init__(self, path: str, clock=time.time, max_events: int = 50000):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._events: dict[str, deque] = {}
        self._first_ts: Optional[float] = None
        self._max = max_events
        self._dirty = False
        self._last_flush = 0.0
        self.load_error: Optional[str] = None
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._first_ts = float(data.get("first_ts") or 0) or None
            for k, vals in (data.get("events") or {}).items():
                self._events[k] = deque((tuple(v) for v in vals), maxlen=self._max)
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"[:200]

    def add(self, key: str, value: float = 1.0) -> None:
        try:
            now = float(self._clock())
            with self._lock:
                if self._first_ts is None:
                    self._first_ts = now
                self._events.setdefault(str(key), deque(maxlen=self._max)).append((now, float(value)))
                self._dirty = True
        except Exception:
            pass

    def flush(self, force: bool = False) -> bool:
        now = float(self._clock())
        with self._lock:
            if not self._dirty and not force:
                return False
            if not force and now - self._last_flush < 30:
                return False
            cutoff = now - self.WINDOWS["24h"]
            data = {"first_ts": self._first_ts,
                    "events": {k: [list(e) for e in v if e[0] >= cutoff] for k, v in self._events.items()}}
            self._dirty = False
            self._last_flush = now
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"))
            os.replace(tmp, self._path)
            return True
        except Exception as exc:
            self.load_error = f"flush {type(exc).__name__}: {exc}"[:200]
            return False

    def snapshot(self) -> dict:
        now = float(self._clock())
        with self._lock:
            keys = {k: list(v) for k, v in self._events.items()}
            first = self._first_ts
        out = {}
        for name, span in self.WINDOWS.items():
            complete = bool(first is not None and now - first >= span)
            out[name] = {"window_complete": complete,
                         "coverage_s": round(min(span, now - first), 1) if first else 0.0,
                         "counts": {k: sum(1 for ts, _ in v if now - ts <= span) for k, v in keys.items()},
                         "sums": {k: round(sum(val for ts, val in v if now - ts <= span), 6) for k, v in keys.items()}}
        return {"schema": COUNTERS_SCHEMA, "path": os.path.basename(self._path), "first_ts": first,
                "load_error": self.load_error, "windows": out}


# ----------------------------------------------------------------------------
# A5 warm-up blind time
# ----------------------------------------------------------------------------
class BlindTime:
    """Per-tile signal-blind seconds (warming up / evaluator not ready), persisted as daily buckets."""

    def __init__(self, path: Optional[str] = None, clock=time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._last: dict[str, tuple] = {}
        self._buckets: dict[str, dict[str, float]] = {}  # lane -> {yyyymmddhh: seconds}
        self._warmup_until: dict[str, Optional[float]] = {}
        if path:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._buckets = {k: {h: float(s) for h, s in v.items()} for k, v in json.load(fh).items()}
            except Exception:
                self._buckets = {}

    def observe(self, lane: str, blind: bool, warmup_until_ts: Optional[float] = None) -> None:
        try:
            now = float(self._clock())
            with self._lock:
                prev = self._last.get(lane)
                if prev is not None and prev[1]:
                    dt = max(0.0, min(5.0, now - prev[0]))
                    hour = time.strftime("%Y%m%d%H", time.gmtime(now))
                    b = self._buckets.setdefault(lane, {})
                    b[hour] = b.get(hour, 0.0) + dt
                self._last[lane] = (now, bool(blind))
                self._warmup_until[lane] = warmup_until_ts
        except Exception:
            pass

    def flush(self) -> None:
        if not self._path:
            return
        now = float(self._clock())
        cutoff = time.strftime("%Y%m%d%H", time.gmtime(now - 48 * 3600))
        with self._lock:
            data = {lane: {h: round(s, 1) for h, s in b.items() if h >= cutoff} for lane, b in self._buckets.items()}
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._path)
        except Exception:
            pass

    def snapshot(self) -> dict:
        now = float(self._clock())
        cutoff = time.strftime("%Y%m%d%H", time.gmtime(now - 24 * 3600))
        tiles, alerts = {}, []
        with self._lock:
            lanes = set(self._buckets) | set(self._last)
            for lane in sorted(lanes):
                mins = sum(s for h, s in (self._buckets.get(lane) or {}).items() if h > cutoff) / 60.0
                last = self._last.get(lane)
                tiles[lane] = {"blind_now": bool(last and last[1]), "warmup_until_ts": self._warmup_until.get(lane),
                               "blind_minutes_24h": round(mins, 1)}
                if mins > 60:
                    alerts.append(f"{lane}:BLIND_GT_60M_24H")
        return {"schema": WARMUP_SCHEMA, "tiles": tiles, "alerts": alerts, "level": AMBER if alerts else GREEN}


# ----------------------------------------------------------------------------
# A6 regime sources
# ----------------------------------------------------------------------------
def normalize_regime(label) -> Optional[str]:
    if label is None:
        return None
    text = str(label).strip().upper()
    if not text or text in ("UNKNOWN", "NONE", "NULL", "WARMUP", "WARMING_UP"):
        return None
    for key, norm in (("VIOL", "VIOLENT"), ("TREND", "TREND"), ("RANGE", "RANGE"), ("CHOP", "RANGE"),
                      ("MEAN", "RANGE"), ("CALM", "RANGE"), ("QUIET", "RANGE")):
        if key in text:
            return norm
    return text


class RegimeComparator:
    def __init__(self, clock=time.time):
        self._clock = clock
        self.disagree_since: Optional[float] = None
        self.bad_bars: deque = deque(maxlen=10)
        self._last_bar_ts = None

    def compare(self, sources: Mapping[str, Mapping]) -> dict:
        now = float(self._clock())
        labels = {name: normalize_regime((src or {}).get("label")) for name, src in sources.items()}
        known = {v for v in labels.values() if v}
        disagree = len(known) > 1
        if disagree:
            self.disagree_since = self.disagree_since or now
        else:
            self.disagree_since = None
        bars = sources.get("regime_bars_3m") or {}
        bar_ts = bars.get("close_ts")
        if bar_ts is not None and bar_ts != self._last_bar_ts:
            self._last_bar_ts = bar_ts
            self.bad_bars.append(bars.get("bar_ok") is False)
        consecutive_bad = 0
        for bad in reversed(self.bad_bars):
            if not bad:
                break
            consecutive_bad += 1
        alerts = []
        dis_for = round(now - self.disagree_since, 1) if self.disagree_since else None
        if dis_for is not None and dis_for > 600:
            alerts.append("REGIME_SOURCES_DISAGREE_GT_10M")
        if consecutive_bad > 3:
            alerts.append("REGIME_BAR_NOT_OK_GT_3_BARS")
        return {"schema": REGIME_SCHEMA, "sources": [{"source": k, **dict(v or {}), "normalized": labels[k]}
                                                     for k, v in sources.items()],
                "agree": not disagree, "disagree_for_s": dis_for, "consecutive_bad_bars": consecutive_bad,
                "alerts": alerts, "level": AMBER if alerts else GREEN}


# ----------------------------------------------------------------------------
# A7 shipper custody
# ----------------------------------------------------------------------------
# BTC-480 / HM (30 Oct): false GREEN when laptop_acked_seq kept advancing while custody_through_seq
# stayed stuck (live: custody_through_seq=5093 since ~24 Oct, shipped_seq≈9.5k). Stall clock is keyed on
# custody_through_seq advancing (CustodyLag observes seq; single-shot falls back to custody_received_at).
# RED alert is the shipped-minus-custody lag after CUSTODY_STALL_RED_SEC with no custody seq advance.
# Never on prune_mode, never a gate. laptop_ack_* fields stay informational / back-compat aliases.
CUSTODY_STALL_RED_SEC = 2 * 3600.0
LAPTOP_ACK_STALL_RED_SEC = CUSTODY_STALL_RED_SEC  # back-compat alias
SHIPPED_MINUS_CUSTODY_LAG = "SHIPPED_MINUS_CUSTODY_LAG"
CUSTODY_SOURCE_UNAVAILABLE = "CUSTODY_SOURCE_UNAVAILABLE"


def _iso_ts(value):
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _seq_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _default_latest_custody(store_root, prefix):
    """Live custody receipt reader — store receipts only; never /head or status.json."""
    import research_segment_prune as prune
    return prune.latest_custody(store_root, prefix)


def resolve_live_custody_for_lag(store_root, prefix, *, reader=None) -> dict:
    """Shared live custody ``through_seq`` reader for lag/stall (R4).

    Never reads ``/head`` or shipper ``status.json`` ``custody_through_seq`` (frozen 5093).
    Returns ``{"ok": True, "through_seq": int, "received_at": ..., "source": "live"}`` or
    ``{"ok": False, "error": CUSTODY_SOURCE_UNAVAILABLE, "source": "unavailable"}``.
    """
    reader = reader or _default_latest_custody
    try:
        if store_root is None or not prefix:
            return {"ok": False, "error": CUSTODY_SOURCE_UNAVAILABLE, "source": "unavailable"}
        receipt = reader(store_root, prefix)
        if not isinstance(receipt, Mapping):
            return {"ok": False, "error": CUSTODY_SOURCE_UNAVAILABLE, "source": "unavailable"}
        seq = _seq_int(receipt.get("through_seq"))
        if seq is None:
            return {"ok": False, "error": CUSTODY_SOURCE_UNAVAILABLE, "source": "unavailable"}
        return {
            "ok": True,
            "through_seq": seq,
            "received_at": receipt.get("received_at"),
            "source": "live",
        }
    except Exception:  # noqa: BLE001 — lag path fails closed as UNAVAILABLE
        return {"ok": False, "error": CUSTODY_SOURCE_UNAVAILABLE, "source": "unavailable"}


def apply_live_custody_to_transfer(transfer: Mapping | None, live: Mapping) -> dict:
    """Re-key transfer lag fields onto live custody; preserve status field as legacy only."""
    t = dict(transfer or {})
    if "custody_through_seq_status" not in t:
        t["custody_through_seq_status"] = t.get("custody_through_seq")
    if live.get("ok"):
        t["custody_through_seq"] = live["through_seq"]
        if live.get("received_at"):
            t["custody_received_at"] = live["received_at"]
        t["custody_source"] = "live"
        t.pop("custody_source_error", None)
    else:
        # Never fall back to status/head 5093 for lag algebra.
        t["custody_through_seq"] = None
        t["custody_source"] = "unavailable"
        t["custody_source_error"] = live.get("error") or CUSTODY_SOURCE_UNAVAILABLE
    return t


def custody_stall(transfer: Mapping | None, now: float, *, seq_advanced_at: float | None = None) -> dict:
    """Custody seq stall: RED when shipped_seq - custody_through_seq > 0 and custody_through_seq has not
    advanced for CUSTODY_STALL_RED_SEC. Stall clock prefers ``seq_advanced_at`` (from CustodyLag when the
    seq last increased); single-shot /health uses ``custody_received_at`` (receipt time for that through
    seq). Never keys on laptop_ack_advanced_at or prune_mode. Alert only, never a gate.

    ``custody_through_seq`` must already be the **live** custody seq (see
    ``apply_live_custody_to_transfer``). Live source unavailable → RED ``CUSTODY_SOURCE_UNAVAILABLE``.
    """
    t = dict(transfer or {})
    last_err = (("SHIPPER: " + str(t.get("last_error")))[:200] if t.get("last_error") else None)
    if t.get("custody_source") == "unavailable" or t.get("custody_source_error") == CUSTODY_SOURCE_UNAVAILABLE:
        return {
            "custody_last_advanced_utc": None,
            "custody_stalled_s": None,
            "custody_stall_reason": CUSTODY_SOURCE_UNAVAILABLE,
            "custody_lag_segments": None,
            "custody_stall_red": True,
            "laptop_ack_stall_red": True,  # back-compat alias
            "custody_last_error": last_err,
        }
    shipped = _seq_int(t.get("shipped_seq"))
    custody = _seq_int(t.get("custody_through_seq"))
    lag = (shipped - custody) if (shipped is not None and custody is not None) else None
    behind = lag is not None and lag > 0
    ts = seq_advanced_at if seq_advanced_at is not None else _iso_ts(t.get("custody_received_at"))
    stalled_s = round(max(0.0, now - ts), 1) if (ts is not None and behind) else (0.0 if ts is not None else None)
    reason = None
    if behind and stalled_s is not None and stalled_s > CUSTODY_STALL_RED_SEC:
        reason = SHIPPED_MINUS_CUSTODY_LAG
    return {"custody_last_advanced_utc": (None if ts is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))),
            "custody_stalled_s": stalled_s,
            "custody_stall_reason": reason,
            "custody_lag_segments": lag,
            "custody_stall_red": reason is not None,
            "laptop_ack_stall_red": reason is not None,  # back-compat alias for /health readers
            "custody_last_error": last_err}


class CustodyLag:
    """Shipper custody lag for /api/monitor/shipper: GREEN, or RED SHIPPED_MINUS_CUSTODY_LAG /
    CUSTODY_SOURCE_UNAVAILABLE (BTC-480 / re-key). Stall clock resets on live custody seq advance (T2b)."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self.samples: deque = deque(maxlen=120)
        self._last_custody_seq: int | None = None
        self._seq_advanced_at: float | None = None

    def observe(self, transfer: Mapping | None) -> dict:
        transfer = dict(transfer or {})
        now = float(self._clock())
        acked, shipped = transfer.get("laptop_acked_seq"), transfer.get("shipped_seq")
        custody = _seq_int(transfer.get("custody_through_seq"))
        if custody is not None:
            if self._last_custody_seq is None:
                self._last_custody_seq = custody
                # Bootstrap from the receipt time for the stuck through-seq when present.
                self._seq_advanced_at = _iso_ts(transfer.get("custody_received_at")) or now
            elif custody > self._last_custody_seq:
                # T2b: a live custody advance resets the stall clock.
                self._last_custody_seq = custody
                self._seq_advanced_at = now
        try:
            ack_lag = int(shipped) - int(acked or 0) if shipped is not None else None
        except (TypeError, ValueError):
            ack_lag = None
        stall = custody_stall(transfer, now, seq_advanced_at=self._seq_advanced_at)
        sample_lag = stall.get("custody_lag_segments")
        if sample_lag is None:
            sample_lag = ack_lag
        if sample_lag is not None and (not self.samples or now - self.samples[-1][0] >= 60):
            self.samples.append((now, sample_lag))
        reason = stall.get("custody_stall_reason")
        alerts = [reason] if stall["custody_stall_red"] and reason else []
        return {"schema": SHIPPER_SCHEMA, **stall, "shipped_seq": shipped, "laptop_acked_seq": acked,
                "ack_lag_segments": ack_lag, "custody_through_seq": transfer.get("custody_through_seq"),
                "custody_through_seq_status": transfer.get("custody_through_seq_status"),
                "custody_source": transfer.get("custody_source"),
                "prune_mode": "off", "retention": "R5A_ELIGIBILITY_NEVER_DELETE",
                "alerts": alerts, "level": RED if alerts else GREEN}
