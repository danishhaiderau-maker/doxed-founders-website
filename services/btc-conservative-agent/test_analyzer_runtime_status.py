"""Regression checks for analyzer startup health and its live status route."""

from pathlib import Path
import json
import sys
import tempfile

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
restart_analyzer = (
    Path(__file__).parents[2] / "scripts" / "analyzer-auto-restart.ps1"
).read_text(encoding="utf-8")
local_analyzer = (
    Path(__file__).parents[2] / "scripts" / "start-local-collection-analyzer.ps1"
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
    "all launchers use the canonical tested analyzer": (
        '@("analyzer_research_engine_v62.py")' in start_analyzer
        and '@("analyzer_research_engine_v62.py")' in restart_analyzer
        and '@("analyzer_research_engine_v62.py")' in local_analyzer
        and 'research\\analyzer_research_engine_v62.py' not in start_analyzer
        and 'research\\analyzer_research_engine_v62.py' not in restart_analyzer
        and 'research\\analyzer_research_engine_v62.py' not in local_analyzer
    ),
    "benchmark lane is not labeled retired": (
        'pathway_status in ("RETIRED", "DATA_RETIRED")' in source
        and 'pathway_status in ("RETIRED", "DATA_RETIRED", "BENCHMARK")' not in source
    ),
    "active tile 2 roster label is current": "SR_MICRO_TILE_V2_STATIC" in source,
    "dashboard refreshes only the active tab": (
        "const SECTION_LOADERS" in source
        and "refreshActiveSection" in source
        and "async function refreshAll" not in source
    ),
    "read-only report APIs use a bounded cache": (
        "_API_CACHE_TTL_SEC" in source and "X-Research-Cache" in source
    ),
    "empty chase isolation is collecting": (
        '"verdict": rep.get("verdict") if has_evidence else "COLLECTING"' in source
    ),
    "MFE cohort cannot be confused with the Type B tile": (
        "MFE Type-B outcome cohort" in source
        and '"Type B Discovery"' not in source
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

original_root = research_dashboard.ROOT
original_data_root = research_dashboard.DATA_ROOT
with tempfile.TemporaryDirectory() as tmp:
    agent_root = Path(tmp)
    research_root = agent_root / "research"
    genome_root = research_root / "genome"
    genome_root.mkdir(parents=True)
    expected_genome = {
        "schema": "trading_genome_analysis_v1",
        "architecture_frozen": "v11.0-genome-architecture-v1",
    }
    (genome_root / "genome_analysis_report.json").write_text(
        json.dumps(expected_genome), encoding="utf-8"
    )
    research_dashboard.ROOT = research_root
    research_dashboard.DATA_ROOT = agent_root
    research_dashboard._API_RESPONSE_CACHE.clear()
    try:
        if research_dashboard._genome_payload() != expected_genome:
            raise SystemExit("failed: dashboard did not resolve canonical Genome artifact")
        with research_dashboard.app.test_client() as client:
            first = client.get("/api/genome")
            second = client.get("/api/genome")
            if first.status_code != 200 or second.headers.get("X-Research-Cache") != "HIT":
                raise SystemExit("failed: analyzer API response cache did not serve a repeat read")
    finally:
        research_dashboard._API_RESPONSE_CACHE.clear()
        research_dashboard.ROOT = original_root
        research_dashboard.DATA_ROOT = original_data_root

with research_dashboard.app.test_client() as client:
    response = client.get("/api/status")
    if response.status_code != 200:
        raise SystemExit(f"failed: /api/status returned {response.status_code}")
    payload = response.get_json()
    if payload.get("runtime_analyzer_sync_id") != payload.get("expected_analyzer_sync_id"):
        raise SystemExit("failed: runtime analyzer identity does not match expected identity")

print(f"PASS: {len(checks)} analyzer startup-health checks + live /api/status")
