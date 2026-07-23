#!/usr/bin/env python3
"""
Home or Railway entry — full research bot + Bitfinex relay hooks for doxxedcrypto.digital.

Prefer running bot.py at home with home-bot.env (see docs/HOME_BOT_MIGRATION.md).
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
    try:
        git_dir = Path(_SERVICE_DIR).parents[1] / ".git"
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
    except OSError:
        pass
    return "unknown"


os.environ.setdefault("SHOWCASE_AGENT", "1")
os.environ.setdefault("HOME_BOT_LOCAL", "1")
os.environ.setdefault("HOME_RESEARCH_FULL", "1")
os.environ.setdefault(
    "SHOWCASE_RELAY_WEBHOOK_URL",
    "https://doxxedcrypto.digital/api/trading-agents/conservative-btc/showcase-relay-event",
)
os.environ.setdefault(
    "SHOWCASE_INFERENCE_USAGE_URL",
    "https://doxxedcrypto.digital/api/internal/showcase-inference-usage",
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

stop_early_ping_server()
register_showcase_ui(signal_engine.app, bot_module=signal_engine, block_warehouse=False)


def main() -> None:
    signal_engine.main()


if __name__ == "__main__":
    main()
