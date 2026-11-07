"""Operational values stay unset. Synthetic callers pass explicit test values."""

from __future__ import annotations

OPERATIONAL_VALUES = {
    "symbol": None,
    "frozen_symbol": None,
    "prec": None,
    "freq": None,
    "length": None,
    "depth_n": None,
    "heartbeat_timeout_s": None,
    "backoff_initial_s": None,
    "backoff_cap_s": None,
    "backoff_jitter_s": None,
    "clock_warn_ms": None,
    "clock_invalid_ms": None,
    "fsync_max_interval_s": None,
    "fsync_max_records": None,
    "segment_max_age_s": None,
    "segment_max_bytes": None,
    "trade_quiet_warn_s": None,
    "disk_cap_bytes": None,
    "queue_cap": None,
    "health_interval_s": None,
    "output_root": None,
    "board_freeze_id": None,
    "clock_source": None,
    "seq_layout": None,
    "seq_scope": None,
}


class CollectorError(Exception):
    pass


class ConfigUnset(CollectorError):
    def __init__(self, name: str):
        super().__init__(f"UNSET:{name}")
        self.name = name
        self.exit_code = 78


class StartRefused(CollectorError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        self.exit_code = 78


class CaptureStopped(CollectorError):
    def __init__(self, reason: str, stderr: str):
        super().__init__(reason)
        self.reason = reason
        self.stderr = stderr
        self.exit_code = 75


class CrashFault(CollectorError):
    def __init__(self, mode: str):
        super().__init__(mode)
        self.mode = mode


def require_set(name: str, value):
    if value is None:
        raise ConfigUnset(name)
    return value


def check_instrument(frozen_symbol, requested_symbol) -> None:
    if frozen_symbol is None or requested_symbol is None:
        raise StartRefused("INSTRUMENT_UNSET")
    if frozen_symbol != requested_symbol:
        raise StartRefused("INSTRUMENT_MISMATCH")


def backoff_delay(attempt: int, *, initial_s, cap_s, jitter_s) -> float:
    """Bounded delay. No sleep. All three inputs are required; none has a default."""
    initial_s = require_set("backoff_initial_s", initial_s)
    cap_s = require_set("backoff_cap_s", cap_s)
    jitter_s = require_set("backoff_jitter_s", jitter_s)
    if attempt < 1:
        raise ValueError("attempt")
    if initial_s < 0 or cap_s < 0 or jitter_s < 0:
        raise ValueError("backoff")
    base = float(initial_s) * (2 ** (attempt - 1))
    delay = base + float(jitter_s)
    if delay > float(cap_s):
        delay = float(cap_s)
    return delay


def classify_clock_offset(offset_ms, *, warn_ms, invalid_ms) -> str:
    warn_ms = require_set("clock_warn_ms", warn_ms)
    invalid_ms = require_set("clock_invalid_ms", invalid_ms)
    magnitude = abs(int(offset_ms))
    if magnitude > int(invalid_ms):
        return "invalid"
    if magnitude > int(warn_ms):
        return "warn"
    return "ok"
