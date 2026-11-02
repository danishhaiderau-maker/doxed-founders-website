"""Day-1 local fresh-start runtime gates (paper only; no Fly ownership).

After Fly ``doxed-btc-bot`` delete (2026-11-03), the laptop may own paper CTRL+GS-01
when ``LOCAL_FRESH_START`` is armed with the exact confirmation phrase. This module
is pure env/path helpers plus health JSON — no orders, no Bitfinex private API.

Strategist: local market series starts at a SERIES BREAK stamp; Fly-era data stays
closed. Gaps (disconnect / laptop sleep) are logged and never filled.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

SCHEMA = "local_fresh_runtime_v1"
HEALTH_SCHEMA = "local_fresh_health_v1"
SERIES_BREAK_SCHEMA = "local_fresh_series_break_v1"
SERIES_BREAK_NAME = "series_break.json"

# Exact opt-in; start scripts must set this (R12 card lists the laptop action).
LOCAL_FRESH_ENV = "LOCAL_FRESH_START"
LOCAL_FRESH_CONFIRMATION = "I_OWN_LOCAL_PAPER_AFTER_FLY_DELETE"
LOCAL_FRESH_CONFIRM_ENV = "LOCAL_FRESH_START_CONFIRM"

# Registry lane tokens (combo_pathway_config) — CTRL + GS-01 only.
LOCAL_FRESH_LANES: tuple[str, ...] = (
    "FAMILY_RANDOM_CONTROL_TAKER_90",
    "FAMILY_GS01_XV_PREMIUM_ATR_TP",
)
LOCAL_FRESH_LANE_LABELS: tuple[str, ...] = ("CTRL", "GS-01")

# Trade / private Bitfinex credentials must be ABSENT on the local soak host.
FORBIDDEN_TRADE_KEY_NAMES: tuple[str, ...] = (
    "BITFINEX_API_KEY",
    "BITFINEX_API_SECRET",
    "BITFINEX_READ_API_KEY",
    "BITFINEX_READ_API_SECRET",
)

DEFAULT_HEALTH_RELATIVE = os.path.join("logs", "local_fresh_health.json")
DEFAULT_DATA_RELATIVE = os.path.join(
    "services", "btc-conservative-agent", "local-fresh-data"
)

# Disk headroom before write (HM B3). Override via LOCAL_FRESH_MIN_DISK_FREE_BYTES.
DEFAULT_MIN_DISK_FREE_BYTES = 512 * 1024 * 1024  # 512 MiB

_LOG = logging.getLogger("local_fresh_runtime")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _log_import_failure(what: str, exc: BaseException) -> None:
    """HM A2 — failed try-imports must log loudly (never silent)."""
    _LOG.error(
        "LOCAL_FRESH_IMPORT_FAILED %s: %s: %s",
        what,
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    print(
        f"LOCAL_FRESH_IMPORT_FAILED {what}: {type(exc).__name__}: {exc}",
        file=sys.stderr,
        flush=True,
    )


def is_local_fresh_start(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    if not _truthy(env.get(LOCAL_FRESH_ENV)):
        return False
    return str(env.get(LOCAL_FRESH_CONFIRM_ENV) or "").strip() == LOCAL_FRESH_CONFIRMATION


def forbidden_trade_keys_present(
    environ: Optional[Mapping[str, str]] = None,
) -> list[str]:
    env = environ if environ is not None else os.environ
    return [
        name
        for name in FORBIDDEN_TRADE_KEY_NAMES
        if str(env.get(name) or "").strip()
    ]


def validate_paper_safe_env(
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[bool, list[str]]:
    """Return (ok, reasons). Fail closed unless paper + disarmed + no trade keys."""
    env = environ if environ is not None else os.environ
    reasons: list[str] = []
    if not is_local_fresh_start(env):
        reasons.append("LOCAL_FRESH_START_NOT_CONFIRMED")
    if not _truthy(env.get("FORCE_PAPER_MODE")):
        reasons.append("FORCE_PAPER_MODE_REQUIRED")
    if _truthy(env.get("BITFINEX_LIVE_ENABLED")):
        reasons.append("BITFINEX_LIVE_MUST_BE_OFF")
    if _truthy(env.get("LIVE_COPY_ARMED")) or _truthy(env.get("RELAY_ARMED")):
        reasons.append("RELAY_OR_LIVE_COPY_MUST_BE_DISARMED")
    keys = forbidden_trade_keys_present(env)
    if keys:
        # Names only (R10) — never values.
        reasons.append("FORBIDDEN_TRADE_KEYS_PRESENT:" + ",".join(keys))
    fly_app = str(env.get("FLY_APP_NAME") or env.get("FLY_APP") or "").strip()
    if fly_app:
        reasons.append("FLY_APP_NAME_MUST_BE_UNSET")
    return (not reasons, reasons)


def allowed_lanes(
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[str, ...]:
    env = environ if environ is not None else os.environ
    raw = str(env.get("LOCAL_FRESH_LANES") or "").strip()
    if not raw:
        return LOCAL_FRESH_LANES
    wanted = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    allowed = tuple(lane for lane in wanted if lane in LOCAL_FRESH_LANES)
    return allowed or LOCAL_FRESH_LANES


def lane_allowed(lane: str, environ: Optional[Mapping[str, str]] = None) -> bool:
    return str(lane or "").strip().upper() in allowed_lanes(environ)


def apply_local_fresh_environ(
    environ: Optional[MutableMapping[str, str]] = None,
) -> dict[str, str]:
    """Force paper-safe defaults for local fresh start. Mutates environ."""
    env: MutableMapping[str, str] = environ if environ is not None else os.environ
    env["FORCE_PAPER_MODE"] = "1"
    env["BITFINEX_LIVE_ENABLED"] = "0"
    env["HOME_BOT_LOCAL"] = "1"
    env["HOME_RESEARCH_FULL"] = "1"
    env["BLOCK_RESEARCH_WAREHOUSE"] = "0"
    env["SHOWCASE_AGENT"] = env.get("SHOWCASE_AGENT") or "1"
    env.pop("LIVE_COPY_ARMED", None)
    env.pop("RELAY_ARMED", None)
    # Clear Fly identity so leftover shell env cannot resurrect Fly control.
    for name in (
        "FLY_APP_NAME",
        "FLY_APP",
        "FLY_MACHINE_ID",
        "FLY_ALLOC_ID",
        "FLY_REGION",
        "FLY_API_TOKEN",
    ):
        env.pop(name, None)
    port = str(env.get("DASHBOARD_PORT") or env.get("PORT") or "7002").strip() or "7002"
    env.setdefault("DASHBOARD_PORT", port)
    env.setdefault("PORT", port)
    env.setdefault("DASHBOARD_BIND_HOST", "127.0.0.1")
    env["DASHBOARD_PUBLIC_URL"] = f"http://127.0.0.1:{port}/"
    env["RESEARCH_DASHBOARD_PUBLIC_URL"] = f"http://127.0.0.1:{port}/analysis"
    # HM + Strategist / RK t781u (+ Danish 01:37 keep basic plans): no Railway/Neon coupling.
    try:
        from local_cloud_decouple import apply_decouple_environ

        apply_decouple_environ(env)
    except Exception as exc:  # noqa: BLE001 — fall back but log loudly (HM A2)
        _log_import_failure("local_cloud_decouple.apply_decouple_environ", exc)
        env["DISABLE_RAILWAY_NEON_COUPLING"] = "1"
        env["LOCAL_SELF_CONTAINED"] = "1"
        env["RELAY_STACK_MODE"] = "research_only"
    return dict(env)


def resolve_data_dir(
    environ: Optional[Mapping[str, str]] = None,
    *,
    repo_root: Optional[str] = None,
) -> Path:
    env = environ if environ is not None else os.environ
    configured = str(env.get("BOT_DATA_DIR") or env.get("LOCAL_FRESH_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
    return (root / DEFAULT_DATA_RELATIVE).resolve()


def resolve_health_path(
    environ: Optional[Mapping[str, str]] = None,
    *,
    repo_root: Optional[str] = None,
) -> Path:
    env = environ if environ is not None else os.environ
    configured = str(env.get("LOCAL_FRESH_HEALTH_JSON") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
    return (root / DEFAULT_HEALTH_RELATIVE).resolve()


def min_disk_free_bytes(environ: Optional[Mapping[str, str]] = None) -> int:
    env = environ if environ is not None else os.environ
    raw = str(env.get("LOCAL_FRESH_MIN_DISK_FREE_BYTES") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_MIN_DISK_FREE_BYTES


def disk_free_bytes(path: str | Path) -> Optional[int]:
    try:
        return int(shutil.disk_usage(str(path)).free)
    except OSError:
        return None


def _windows_rss_bytes() -> Optional[int]:
    """HM N3 — WorkingSetSize via GetProcessMemoryInfo (no psutil required)."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        psapi = ctypes.WinDLL("psapi")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_mem = psapi.GetProcessMemoryInfo
        get_mem.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        get_mem.restype = wintypes.BOOL
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        handle = kernel32.GetCurrentProcess()
        if not get_mem(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def process_rss_bytes() -> Optional[int]:
    try:
        statm = Path("/proc/self/statm").read_text(encoding="ascii").split()
        if len(statm) >= 2:
            return int(statm[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    try:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux ru_maxrss is KiB; macOS is bytes.
        if sys.platform == "darwin":
            return peak
        if sys.platform.startswith("win"):
            # resource may be absent or meaningless on Windows — fall through.
            pass
        else:
            return peak * 1024
    except (ImportError, OSError, ValueError, AttributeError):
        pass
    if sys.platform.startswith("win"):
        return _windows_rss_bytes()
    return None


def ensure_disk_headroom(
    path: str | Path,
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[bool, Optional[int], int]:
    """Return (ok, free_bytes, min_required).

    HM AMBER / RK: **fail closed** when free cannot be measured or is below min.
    """
    minimum = min_disk_free_bytes(environ)
    free = disk_free_bytes(path)
    if free is None:
        return False, None, minimum
    return free >= minimum, free, minimum


def paper_forced_from_env(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return _truthy(env.get("FORCE_PAPER_MODE"))


def disarmed_from_env(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when live/arm bits are off (real env — not hardcoded True)."""
    env = environ if environ is not None else os.environ
    if _truthy(env.get("BITFINEX_LIVE_ENABLED")):
        return False
    if _truthy(env.get("LIVE_COPY_ARMED")) or _truthy(env.get("RELAY_ARMED")):
        return False
    if _truthy(env.get("LIVE_ARMED")) or _truthy(env.get("BITFINEX_LIVE_ARMED")):
        return False
    return True


def series_break_path(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser().resolve() / SERIES_BREAK_NAME


def read_series_break(data_dir: str | Path) -> Optional[dict[str, Any]]:
    path = series_break_path(data_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.error("SERIES_BREAK_READ_FAILED: %s", exc)
        return None


def ensure_series_break(
    data_dir: str | Path,
    *,
    now: Optional[float] = None,
    source_git_rev: Optional[str] = None,
) -> dict[str, Any]:
    """Create SERIES BREAK marker once; refuse mixing unmarked Fly-era SQLite.

    Strategist: start the local series at a new SERIES BREAK stamp; keep Fly-era
    data closed. If ``local_market.sqlite3`` exists without a series_break.json,
    fail closed — operator must move/close Fly-era files outside the local dir.
    """
    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = series_break_path(root)
    existing = read_series_break(root)
    if existing:
        return existing
    sqlite_path = root / "local_market.sqlite3"
    if sqlite_path.is_file() and sqlite_path.stat().st_size > 0:
        raise RuntimeError(
            "SERIES_BREAK_REQUIRED: local_market.sqlite3 exists without "
            f"{SERIES_BREAK_NAME}. Move/close Fly-era data; do not continue this "
            "directory as a local series. Strategist: never mix Fly-era tape."
        )
    ts = float(now if now is not None else time.time())
    payload = {
        "schema": SERIES_BREAK_SCHEMA,
        "series_break_ts": ts,
        "series_break_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
        "fly_era_data": "CLOSED",
        "note": (
            "Local paper series starts here. Fly-era collector/tape must stay "
            "closed and must not be joined or gap-filled into this series."
        ),
        "source_git_rev": (source_git_rev or os.getenv("SOURCE_GIT_REV") or "")[:12]
        or None,
        "lanes": list(LOCAL_FRESH_LANES),
    }
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".series_break.", suffix=".tmp", dir=str(root))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return payload


def build_health_payload(
    *,
    ok: bool,
    reasons: Sequence[str] = (),
    lanes: Optional[Sequence[str]] = None,
    data_dir: Optional[str] = None,
    gap_log_path: Optional[str] = None,
    collector: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
    extra: Optional[Mapping[str, Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
    last_tick_ts: Optional[float] = None,
) -> dict[str, Any]:
    """Build local health JSON. force_paper / disarmed read from real env (HM B2)."""
    env = environ if environ is not None else os.environ
    ts = float(now if now is not None else time.time())
    force_paper = paper_forced_from_env(env)
    disarmed = disarmed_from_env(env)
    live_on = _truthy(env.get("BITFINEX_LIVE_ENABLED"))
    live_copy = _truthy(env.get("LIVE_COPY_ARMED"))
    relay_armed = _truthy(env.get("RELAY_ARMED"))

    tick_ts = last_tick_ts
    if tick_ts is None and collector:
        tick_ts = collector.get("last_tick_ts")
        if tick_ts is None:
            store = collector.get("store") if isinstance(collector.get("store"), dict) else {}
            last_book = store.get("last_book_sec")
            if last_book is not None:
                tick_ts = float(last_book)
    last_tick_age_s: Optional[float] = None
    if tick_ts is not None:
        try:
            last_tick_age_s = max(0.0, ts - float(tick_ts))
        except (TypeError, ValueError):
            last_tick_age_s = None

    disk_free: Optional[int] = None
    disk_ok = True
    disk_min = min_disk_free_bytes(env)
    probe = data_dir or str(Path.cwd())
    try:
        disk_ok, disk_free, disk_min = ensure_disk_headroom(probe, env)
    except Exception as exc:  # noqa: BLE001
        _LOG.error("DISK_HEADROOM_PROBE_FAILED: %s", exc)

    rss = process_rss_bytes()
    series = read_series_break(data_dir) if data_dir else None

    payload: dict[str, Any] = {
        "schema": HEALTH_SCHEMA,
        "ts": ts,
        "ok": bool(ok) and disk_ok and force_paper and disarmed and (not live_on),
        "mode": "LOCAL_FRESH_PAPER",
        "force_paper_mode": force_paper,
        "paper_forced": force_paper,  # HM soak alias — real env, not hardcoded
        "bitfinex_live_enabled": live_on,
        "live_copy_armed": live_copy,
        "relay_armed": relay_armed,
        "disarmed": disarmed,
        "fly_owner": False,
        "lanes": list(lanes or LOCAL_FRESH_LANES),
        "lane_labels": list(LOCAL_FRESH_LANE_LABELS),
        "reasons": list(reasons),
        "data_dir": data_dir,
        "gap_log_path": gap_log_path,
        "collector": dict(collector or {}),
        "trade_keys_absent": not bool(forbidden_trade_keys_present(env)),
        "last_tick_ts": tick_ts,
        "last_tick_age_s": last_tick_age_s,
        "disk_free_bytes": disk_free,
        "disk_min_free_bytes": disk_min,
        "disk_headroom_ok": disk_ok,
        "rss_bytes": rss,
        "series_break": series,
        "gaps_never_filled": True,
    }
    if not disk_ok:
        reason = "DISK_FREE_UNMEASURABLE" if disk_free is None else "DISK_FREE_BELOW_MIN"
        payload["reasons"] = list(payload["reasons"]) + [reason]
    if extra:
        payload.update(dict(extra))
    return payload


def write_health_json(path: str | Path, payload: Mapping[str, Any]) -> str:
    """Atomic write of local health JSON. Returns absolute path written."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, candidate = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, target)
        candidate = ""
    finally:
        if candidate:
            try:
                os.unlink(candidate)
            except OSError:
                pass
    return str(target)


def refuse_message(reasons: Sequence[str]) -> str:
    return "REFUSED_LOCAL_FRESH_START: " + ";".join(reasons)
