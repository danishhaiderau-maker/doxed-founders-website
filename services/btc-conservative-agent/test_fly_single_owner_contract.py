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
desktop_mirror = json.loads(
    (ROOT / "config" / "home-showcase.lock.json").read_text()
)
local_collection = json.loads(
    (ROOT / "config" / "local-collection.lock.json").read_text()
)

assert lock["frozen"] is True
assert lock["desktopBotEnabled"] is False
assert lock["sourceUrl"] == "https://doxed-btc-bot.fly.dev"
assert architecture["runtimeRoles"]["fly"]["aiOwner"] is True
assert architecture["runtimeRoles"]["desktop"]["aiOwner"] is False
assert architecture["canonicalSource"]["processEntrypoint"] == "btc_conservative_agent.py"
assert architecture["canonicalSource"]["strategyModule"] == "bot.py"
assert desktop_mirror["mode"] == "fly-mirror"
assert desktop_mirror["authoritative"] is False
assert desktop_mirror["disableLocalStrategy"] is True
assert desktop_mirror["disableTunnel"] is True
assert local_collection["enabled"] is False
assert local_collection["disableLocalStrategy"] is True

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
for analyzer_launcher in ("start-home-analyzer.ps1", "analyzer-auto-restart.ps1"):
    analyzer_text = text(analyzer_launcher)
    assert '$env:RESEARCH_DASHBOARD_BIND_HOST = "127.0.0.1"' in analyzer_text
    assert '"BITFINEX_API_KEY"' in analyzer_text
    assert "Remove-Item -LiteralPath" in analyzer_text
    assert "allowedAnalyzerVars" in analyzer_text
    assert 'Set-Item -Path ("env:" + $matches[1].Trim())' not in analyzer_text
sync_loop = text("sync-fly-bot-data-loop.ps1")
assert "Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl" in sync_loop
assert "$env:BOT_ADMIN_TOKEN" in sync_loop
assert 'Set-Item -Path ("env:" + $matches[1].Trim())' not in sync_loop
assert "AI" not in text("fly-dashboard-proxy.py").replace(
    "contains no strategy, exchange, or AI code", ""
)
assert "python bot.py" not in (
    ROOT / "services" / "btc-conservative-agent" / "start.ps1"
).read_text(encoding="utf-8")
assert "REFUSED_NON_FLY_RUNTIME" in (
    ROOT / "services" / "btc-conservative-agent" / "start.ps1"
).read_text(encoding="utf-8")
assert "desktop strategy environment export is disabled" in text(
    "print-home-bot-env.mjs"
)
assert "Railway showcase credential push is disabled" in text(
    "push-showcase-bot-credentials.mjs"
)
assert "railwayBotControl: 'disabled'" in text("railway-showcase-control.mjs")
assert "Local strategy lab is disabled" in text("home-stack-local-lab.ps1")
assert "REFUSED_LEGACY_TUNNEL" in text("refuse-legacy-tunnel.ps1")
assert "fly-canonical.lock.json" in text("setup-named-tunnel-api.mjs")

print("Fly single-owner desktop mirror contract checks passed")
