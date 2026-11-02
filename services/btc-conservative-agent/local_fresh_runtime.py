"""Day-1 local fresh-start runtime gates (paper only; no Fly ownership).

After Fly ``doxed-btc-bot`` delete (2026-11-03), the laptop may own paper CTRL+GS-01
when ``LOCAL_FRESH_START`` is armed with the exact confirmation phrase. This module
is pure env/path helpers plus health JSON — no orders, no Bitfinex private API.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

SCHEMA = "local_fresh_runtime_v1"
HEALTH_SCHEMA = "local_fresh_health_v1"

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


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


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
    # HM + Strategist / RK t781u: no Railway/Neon coupling for local paper.
    try:
        from local_cloud_decouple import apply_decouple_environ

        apply_decouple_environ(env)
    except Exception:
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
) -> dict[str, Any]:
    ts = float(now if now is not None else time.time())
    payload: dict[str, Any] = {
        "schema": HEALTH_SCHEMA,
        "ts": ts,
        "ok": bool(ok),
        "mode": "LOCAL_FRESH_PAPER",
        "force_paper_mode": True,
        "bitfinex_live_enabled": False,
        "live_copy_armed": False,
        "relay_armed": False,
        "disarmed": True,
        "fly_owner": False,
        "lanes": list(lanes or LOCAL_FRESH_LANES),
        "lane_labels": list(LOCAL_FRESH_LANE_LABELS),
        "reasons": list(reasons),
        "data_dir": data_dir,
        "gap_log_path": gap_log_path,
        "collector": dict(collector or {}),
        "trade_keys_absent": True,
    }
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
