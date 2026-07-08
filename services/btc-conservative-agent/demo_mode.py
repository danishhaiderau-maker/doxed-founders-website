#!/usr/bin/env python3
"""
Demo launcher for the BTC conservative agent.

Single source of truth for demo-mode bot configuration. Used by the
end-to-end demo harness (`scripts/demo-harness.mjs`) to start the bot
on :7002 in a safe simulation state.

It:
  - Forces LIVE_TRADING_ENABLED=False (refuses to launch if the outer
    shell already set it to True — that would indicate a misconfigured
    environment).
  - Enables demo mode + funding simulation.
  - Picks the cassette mode (replay by default, capture via DEMO_CAPTURE=1
    or --capture).
  - Sets DASHBOARD_PORT=7002 to match the orchestrator.
  - Adds lightweight DeepSeek cassette lookup/record helpers that mirror
    the Node-side cassette-store.ts so AI verdicts are deterministic
    across both runtimes.
  - execs bot.py via runpy so this file is a drop-in replacement for
    `python bot.py`.

Inert unless DEMO_MODE_ENABLED=true. Never enable live trading here.
"""
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CASSETTE_DIR = Path(
    os.environ.get("DEMO_CASSETTE_DIR") or (REPO_ROOT / "cassettes")
)


def _bool_env(name: str, default: str = "False") -> str:
    return os.environ.get(name, default)


def configure_demo_env() -> None:
    if str(os.environ.get("LIVE_TRADING_ENABLED", "False")).strip().lower() == "true":
        sys.stderr.write(
            "\n[demo_mode.py] FATAL: LIVE_TRADING_ENABLED=true detected in the outer "
            "environment.\nThe demo harness must run with live trading disabled. "
            "Aborting.\n"
        )
        sys.exit(2)

    os.environ["LIVE_TRADING_ENABLED"] = "False"
    os.environ["DEMO_MODE_ENABLED"] = "true"
    os.environ["FUNDING_SIMULATION_ENABLED"] = "True"
    os.environ.setdefault("EXECUTION_PAUSED", "True")

    capture = (
        "--capture" in sys.argv
        or str(os.environ.get("DEMO_CAPTURE", "")).strip() == "1"
    )
    os.environ["DEMO_CASSETTE_MODE"] = "capture" if capture else "replay"

    os.environ.setdefault("DASHBOARD_PORT", "7002")
    os.environ.setdefault("LAB_SHADOW_SL_AVOIDANCE_V1", "1")
    os.environ.setdefault("LAB_SHADOW_SIZED_CONTINUOUS_V1", "1")


def cassette_lookup(model: str, temperature: float, prompt_prefix: str):
    """Return a stored DeepSeek cassette entry, or None on miss."""
    key = deepseek_key(model, temperature, prompt_prefix)
    path = CASSETTE_DIR / "deepseek" / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cassette_record(model: str, temperature: float, prompt_prefix: str, response, request=None):
    """Persist a DeepSeek cassette entry. Only writes in capture mode."""
    if os.environ.get("DEMO_CASSETTE_MODE", "replay") != "capture":
        return None
    key = deepseek_key(model, temperature, prompt_prefix)
    path = CASSETTE_DIR / "deepseek" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "key": key,
        "capturedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "mode": "capture",
        "request": request,
        "response": response,
    }
    path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    return entry


def deepseek_key(model: str, temperature: float, prompt_prefix: str) -> str:
    """Stable sha1 key. Must match cassette-store.ts deepseekKey()."""
    prefix = (prompt_prefix or "")[:256]
    raw = f"{model}|{temperature}|{prefix}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def main() -> None:
    configure_demo_env()
    bot_path = Path(__file__).resolve().parent / "bot.py"
    if not bot_path.exists():
        sys.stderr.write(f"[demo_mode.py] FATAL: bot.py not found at {bot_path}\n")
        sys.exit(2)

    # Run bot.py in-process so env vars propagate and signal handling stays
    # consistent. sys.argv[0] is rewritten so bot.py sees its own name.
    sys.argv = [str(bot_path)] + sys.argv[1:]
    runpy.run_path(str(bot_path), run_name="__main__")


if __name__ == "__main__":
    main()
