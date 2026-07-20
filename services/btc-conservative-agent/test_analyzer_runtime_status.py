"""Regression checks for analyzer startup health and its live status route."""

from pathlib import Path
import sys

source = (
    Path(__file__).parent / "research" / "research_dashboard.py"
).read_text(encoding="utf-8")
health = (
    Path(__file__).parents[2] / "scripts" / "home-stack-health.ps1"
).read_text(encoding="utf-8")
launcher = (
    Path(__file__).parents[2] / "scripts" / "home-stack-launcher.ps1"
).read_text(encoding="utf-8")
bridge_recovery = (
    Path(__file__).parents[2] / "scripts" / "ensure-home-bridge.ps1"
).read_text(encoding="utf-8")
start_all = (
    Path(__file__).parents[2] / "scripts" / "home-stack-start-everything.ps1"
).read_text(encoding="utf-8")
start_bot = (
    Path(__file__).parents[2] / "scripts" / "start-home-bot.ps1"
).read_text(encoding="utf-8")
start_analyzer = (
    Path(__file__).parents[2] / "scripts" / "start-home-analyzer.ps1"
).read_text(encoding="utf-8")

checks = {
    "status exposes live analyzer identity": '"runtime_analyzer_sync_id"' in source,
    "status separates report identity": '"report_analyzer_sync_id"' in source,
    "status exposes current-pass grace": '"report_sync_pending"' in source,
    "health rejects wrong runtime identity": "$s.runtime_sync_match -ne $true" in health,
    "health accepts only synced report or bounded current pass": (
        "$s.report_sync_match -eq $true" in health
        and "$s.report_sync_pending -eq $true" in health
    ),
    "bridge records its owning PID": '".home-bridge.pid"' in launcher,
    "bridge recovery uses PID without CIM": (
        '".home-bridge.pid"' in bridge_recovery
        and "Get-CimInstance" not in bridge_recovery
    ),
    "one-click startup hot path avoids CIM": (
        "Get-CimInstance" not in start_all
        and "Get-CimInstance" not in start_bot
        and "Get-CimInstance" not in start_analyzer
    ),
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
if failed:
    raise SystemExit(f"failed: {', '.join(failed)}")

research_dir = Path(__file__).parent / "research"
sys.path.insert(0, str(research_dir))
import research_dashboard

with research_dashboard.app.test_client() as client:
    response = client.get("/api/status")
    if response.status_code != 200:
        raise SystemExit(f"failed: /api/status returned {response.status_code}")
    payload = response.get_json()
    if payload.get("runtime_analyzer_sync_id") != payload.get("expected_analyzer_sync_id"):
        raise SystemExit("failed: runtime analyzer identity does not match expected identity")

print(f"PASS: {len(checks)} analyzer startup-health checks + live /api/status")
