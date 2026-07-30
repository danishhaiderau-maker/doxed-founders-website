from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def text(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


lock = json.loads((ROOT / "config" / "fly-canonical.lock.json").read_text())
architecture = json.loads(
    (ROOT / "config" / "bot-architecture.lock.json").read_text()
)

assert lock["frozen"] is True
assert lock["desktopBotEnabled"] is False
assert lock["sourceUrl"] == "https://doxed-btc-bot.fly.dev"
assert architecture["runtimeRoles"]["fly"]["aiOwner"] is True
assert architecture["runtimeRoles"]["desktop"]["aiOwner"] is False

for guarded in (
    "start-home-bot.ps1",
    "bot-auto-restart.ps1",
    "home-stack-supervisor.ps1",
    "home-stack-supervisor-watchdog.ps1",
    "relay-state-pusher.ps1",
    "stack-monitor.ps1",
    "home-stack-start-everything.ps1",
):
    assert "fly-canonical.lock.json" in text(guarded), guarded

assert "start-fly-desktop-mirror.ps1" in text("start-showcase-bot.cmd")
assert "fly-dashboard-proxy.py" in text("start-fly-desktop-mirror.ps1")
assert "sync-fly-bot-data-loop.ps1" in text("start-fly-desktop-mirror.ps1")
assert "$env:BTC_AGENT_DATA_DIR = $analyzerDataDir" in text(
    "start-home-analyzer.ps1"
)
assert "$env:BTC_AGENT_DATA_DIR = $analyzerDataDir" in text(
    "analyzer-auto-restart.ps1"
)
assert "AI" not in text("fly-dashboard-proxy.py").replace(
    "contains no strategy, exchange, or AI code", ""
)

print("Fly single-owner desktop mirror contract checks passed")
