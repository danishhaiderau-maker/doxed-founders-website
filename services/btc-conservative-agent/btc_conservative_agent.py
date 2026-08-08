#!/usr/bin/env python3
"""
Fly.io-only production entry point for the Conservative BTC bot.

Fly.io is the only supported production runtime. Desktop processes are
dashboard, data-mirror, and analyzer services only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


_STARTUP_LOG_HANDLES = []


def _attach_startup_logs() -> None:
    """Keep detached startup tracebacks inspectable on Windows.

    PowerShell's RedirectStandardOutput/RedirectStandardError path rebuilds the
    environment in a case-insensitive dictionary. Some Windows hosts expose
    both ``Path`` and ``PATH``, causing Start-Process to fail before Python is
    created. Opening the files inside Python avoids that launcher failure.
    """
    for stream_name, env_name in (
        ("stdout", "BOT_STARTUP_STDOUT_LOG"),
        ("stderr", "BOT_STARTUP_STDERR_LOG"),
    ):
        path = (os.getenv(env_name) or "").strip()
        if not path:
            continue
        try:
            handle = open(path, "a", encoding="utf-8", buffering=1)
            _STARTUP_LOG_HANDLES.append(handle)
            setattr(sys, stream_name, handle)
        except OSError:
            # Diagnostic logging must never prevent the trading bot from booting.
            continue


_attach_startup_logs()


def _read_boot_revision() -> str:
    """Read the checkout revision before the full bot import.

    The home supervisor validates owner + revision on every health probe. The
    temporary boot server must therefore identify the same checkout as the
    full Flask app or a correct, still-loading bot is mistaken for a stale one.
    """
    env_revision = (os.getenv("SOURCE_GIT_REV") or "").strip()
    if env_revision:
        return env_revision[:12]
    try:
        service_dir = Path(_SERVICE_DIR).resolve()
        git_dir = next(
            (candidate / ".git" for candidate in (service_dir, *service_dir.parents)
             if (candidate / ".git").exists()),
            service_dir / ".git",
        )
        if git_dir.is_file():
            raw_git_dir = git_dir.read_text(encoding="utf-8").strip()
            if raw_git_dir.lower().startswith("gitdir:"):
                git_dir = (git_dir.parent / raw_git_dir.split(":", 1)[1].strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:12] if head else "unknown"
        ref_name = head.split(":", 1)[1].strip()
        ref_path = git_dir / Path(ref_name)
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip()[:12]
        packed_refs = git_dir / "packed-refs"
        if packed_refs.is_file():
            for line in packed_refs.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name == ref_name:
                    return sha.strip()[:12]
    except (OSError, IndexError):
        pass
    return "unknown"


_fly_app = (os.getenv("FLY_APP_NAME") or "").strip()
_fly_machine = (
    os.getenv("FLY_MACHINE_ID")
    or os.getenv("FLY_ALLOC_ID")
    or ""
).strip()
_fly_region = (os.getenv("FLY_REGION") or "").strip()
if (
    _fly_app != "doxed-btc-bot"
    or not _fly_machine
    or not _fly_region
):
    print(
        "REFUSED_NON_FLY_RUNTIME: Fly.io app doxed-btc-bot is the sole AI, "
        "strategy, paper-execution, and relay-signal owner. Use the desktop "
        "mirror launchers for dashboard/analyzer access. Exact Fly app, "
        "machine, and region identity is required.",
        file=sys.stderr,
    )
    raise SystemExit(78)

_required_fly_controls = (
    "BOT_ADMIN_TOKEN",
    "BOT_CONTROL_SECRET",
    "SHOWCASE_WEBHOOK_SECRET",
    "SHOWCASE_RELAY_WEBHOOK_URL",
)
_missing_fly_controls = [
    name for name in _required_fly_controls
    if not (os.getenv(name) or "").strip()
]
if _missing_fly_controls:
    print(
        "REFUSED_MISSING_FLY_CONTROL: canonical Fly production requires "
        + ", ".join(_missing_fly_controls),
        file=sys.stderr,
    )
    raise SystemExit(78)

if (os.getenv("FORCE_PAPER_MODE") or "").strip().lower() not in (
    "1",
    "true",
    "yes",
    "on",
):
    print(
        "REFUSED_DIRECT_FLY_LIVE: Fly is the paper-signal owner only; "
        "Railway is the isolated Bitfinex live executor.",
        file=sys.stderr,
    )
    raise SystemExit(78)

os.environ.setdefault("SHOWCASE_AGENT", "1")
# Adoption chart feed — DeepSeek usage POSTs here after each trading inference.
# Restored after the Jul 31 Fly-only rewrite dropped the previous setdefault and
# left Fly without SHOWCASE_INFERENCE_USAGE_URL, which zeroed the landing chart.
os.environ.setdefault(
    "SHOWCASE_INFERENCE_USAGE_URL",
    "https://doxxedcrypto.digital/api/internal/showcase-inference-usage",
)
# Fly is the canonical strategy/trading owner. It must never inherit the old
# desktop/full-warehouse identity merely because this shared entry module was
# historically used in multiple environments.
os.environ["HOME_BOT_LOCAL"] = "0"
os.environ["HOME_RESEARCH_FULL"] = "0"
os.environ.setdefault("BLOCK_RESEARCH_WAREHOUSE", "1")
os.environ["DASHBOARD_PUBLIC_URL"] = "https://doxed-btc-bot.fly.dev/"
os.environ["RESEARCH_DASHBOARD_PUBLIC_URL"] = (
    "https://doxed-btc-bot.fly.dev/analysis"
)

_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

_port = int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or "7002")
if _port == 7810:
    raise SystemExit(
        "DASHBOARD_PORT=7810 is the home bridge, not the bot. "
        "Use start-home-bot.ps1 -Port 7002 or Reset home stack from Agent Hub."
    )
_bind_host = os.getenv("DASHBOARD_BIND_HOST", "0.0.0.0")
_boot_version = "booting"
_boot_revision = _read_boot_revision()

try:
    from combo_pathway_config import EXECUTION_FIX_VERSION as _boot_version  # noqa: E402
except Exception:
    pass

from early_boot import start_early_ping_server, stop_early_ping_server  # noqa: E402

start_early_ping_server(
    _port,
    version=_boot_version,
    host=_bind_host,
    source_git_rev=_boot_revision,
)

import bot as signal_engine  # noqa: E402 — synced research engine (signal backend)
from showcase_ui import register_showcase_ui  # noqa: E402

register_showcase_ui(signal_engine.app, bot_module=signal_engine, block_warehouse=None)


def main() -> None:
    # Keep instant liveness available through the potentially slow route/UI
    # registration above. Release :7002 only at the final handoff; bot.main()
    # binds the full bounded server before it restores persistent state.
    stop_early_ping_server()
    signal_engine.main()


if __name__ == "__main__":
    main()
