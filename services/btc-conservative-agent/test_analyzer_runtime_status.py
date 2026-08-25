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
stack_common = (
    Path(__file__).parents[2] / "scripts" / "home-stack-common.ps1"
).read_text(encoding="utf-8")
stack_supervisor = (
    Path(__file__).parents[2] / "scripts" / "home-stack-supervisor.ps1"
).read_text(encoding="utf-8")
supervisor_watchdog = (
    Path(__file__).parents[2] / "scripts" / "home-stack-supervisor-watchdog.ps1"
).read_text(encoding="utf-8")
bot_auto_restart = (
    Path(__file__).parents[2] / "scripts" / "bot-auto-restart.ps1"
).read_text(encoding="utf-8")
bot_crash_monitor = (
    Path(__file__).parents[2] / "scripts" / "bot-crash-monitor.ps1"
).read_text(encoding="utf-8")
analyzer_engine = (
    Path(__file__).parent / "analyzer_research_engine_v62.py"
).read_text(encoding="utf-8")
lane_roster = (
    Path(__file__).parent / "pathway_lane_roster.py"
).read_text(encoding="utf-8")

checks = {
    "analyzer fails closed without canonical mirror": (
        'BTC_AGENT_DATA_DIR is required and must point to the ' in analyzer_engine
        and 'refusing to generate reports from cwd' in analyzer_engine
        and 'if not os.path.isdir(_configured_data_root)' in analyzer_engine
    ),
    "status exposes live analyzer identity": '"runtime_analyzer_sync_id"' in source,
    "status separates report identity": '"report_analyzer_sync_id"' in source,
    "status exposes current-pass grace": '"report_sync_pending"' in source,
    "lightweight health route avoids report reads": (
        '@app.route("/api/health")' in source
        and '"report_root": str(ROOT)' in source
        and '"data_root": str(DATA_ROOT)' in source
    ),
    "health rejects wrong runtime identity": "$s.runtime_sync_match -ne $true" in health,
    "health validates the canonical report root": (
        '$s.report_root' in health
        and "$expectedReportRoot = [System.IO.Path]::GetFullPath($agentDir)" in health
        and "$actualReportRoot.Equals($expectedReportRoot" in health
    ),
    "retired restart monitor cannot create a competing owner": (
        "disabled fail-closed" in restart_analyzer
        and "Start-Process" not in restart_analyzer
        and "Set-Content" not in restart_analyzer
        and 'AddSeconds(180)' in start_all
    ),
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
    "stale dashboard recovery never terminates the analyzer engine owner": (
        '".home-analyzer-dashboard.pid"' in start_analyzer[
            start_analyzer.index("Port $AnalyzerPort has a stale dashboard listener") :
            start_analyzer.index("# Publish the read-only dashboard")
        ]
        and '".home-analyzer.pid"' not in start_analyzer[
            start_analyzer.index("Port $AnalyzerPort has a stale dashboard listener") :
            start_analyzer.index("# Publish the read-only dashboard")
        ]
    ),
    "all launchers use the canonical tested analyzer": (
        '@("analyzer_research_engine_v62.py")' in start_analyzer
        and '@("analyzer_research_engine_v62.py")' in local_analyzer
        and '$env:BTC_AGENT_REPORT_DIR = $agentDir' in start_analyzer
        and 'research\\analyzer_research_engine_v62.py' not in start_analyzer
        and 'research\\analyzer_research_engine_v62.py' not in restart_analyzer
        and 'research\\analyzer_research_engine_v62.py' not in local_analyzer
    ),
    "dashboard refreshes only the active tab": (
        "const SECTION_LOADERS" in source
        and "refreshActiveSection" in source
        and "async function refreshAll" not in source
    ),
    "Genome has a truthful async loading state": (
        'id="genome-empty">Loading the current Genome report' in source
        and 'id="genome-content" style="display:none"' in source
    ),
    "Genome schema cannot be mistaken for the running release": (
        "Genome schema (not release)" in source
        and "independent frozen research-data contract" in source
        and "Running release" in source
    ),
    "dashboard favicon probe is quiet": '@app.route("/favicon.ico")' in source,
    "read-only report APIs use a bounded cache": (
        "_API_CACHE_TTL_SEC" in source and "X-Research-Cache" in source
    ),
    "dashboard resolves reports from the active analyzer root": (
        "BTC_AGENT_REPORT_DIR" in source
        and '_CWD_ROOT / "analyzer_research_engine_v62.py"' in source
    ),
    "lane aggregation stays stale-while-refreshed": (
        "prime_dashboard_caches" in source
        and "research-opportunity-cache" in source
    ),
    "dashboard is isolated from heavy analyzer passes": (
        "subprocess.Popen(" in analyzer_engine
        and '[sys.executable, dashboard_script, "--standalone"]' in analyzer_engine
        and "stdout=subprocess.DEVNULL" in analyzer_engine
        and "research_dashboard_cache_warm" not in analyzer_engine
        and '@("research_dashboard.py", "--standalone")' in start_analyzer
        and '".home-analyzer-dashboard.pid"' in start_analyzer
        and "-WindowStyle Hidden -PassThru" in start_analyzer
    ),
    "analyzer health requires both dashboard and analyzer owner": (
        '".home-analyzer.pid"' in health
        and "if ($analyzerPid -le 0)" in health
        and "Get-Process -Id $analyzerPid" not in health
    ),
    "supervisor recovery replaces monitors with one hidden owner": (
        'Stop-RecordedProcess (Join-Path $repoRoot ".home-bot-crash-monitor.pid")' in stack_supervisor
        and 'Stop-RecordedProcess (Join-Path $repoRoot ".home-analyzer-crash-monitor.pid")' in stack_supervisor
        and stack_supervisor.count("Start-HiddenPs1 -ScriptPath") >= 2
        and stack_supervisor.count('"-NoWait"') >= 2
        and "RECOVER bot - stop + start" not in stack_supervisor
        and "RECOVER analyzer - stop + start" not in stack_supervisor
    ),
    "crash reports derive the canonical stack version": (
        "function Get-ResearchStackVersion" in stack_common
        and "Get-ResearchStackVersion" in start_bot
        and "Get-ResearchStackVersion" in bot_auto_restart
        and "Get-ResearchStackVersion" in bot_crash_monitor
        and "v11.1-virtual-chase-known-combos-v1" not in (
            start_bot + bot_auto_restart + bot_crash_monitor
        )
    ),
    "scheduled supervisor watchdog uses valid parameters": (
        '"$supervisorScript`" -BotPort $botPort -AnalyzerPort $analyzerPort -BridgePort $bridgePort"'
        in supervisor_watchdog
        and "-BridgePort $bridgePort -Quiet" not in supervisor_watchdog
    ),
    "executive summary retains expected-vs-actual calibration": (
        'eva = ai_cal.get("expected_vs_actual") or {}' in analyzer_engine
        and '"expected_vs_actual": eva' in analyzer_engine
    ),
    "terminal hygiene preserves launched services": (
        "function Close-StaleOrchestratorConsoles" in stack_common
        and "Stop-Process -Id $_.Id -Force" in stack_common
        and '"Doxed Wire to Site",\n    "Doxed Auto-Wire"' in stack_common
    ),
    "lifecycle cleanup has restricted-session listener fallback": (
        "function Get-ListenPortOwners" in stack_common
        and "[HomeStackNativeProcess]::GetTcpListenerOwners" in stack_common
        and "function Stop-RecordedProcess" in stack_common
        and '.home-bot-crash-monitor.pid' in stack_common
        and '.home-analyzer-crash-monitor.pid' in (
            Path(__file__).parents[2] / "scripts" / "home-stack-cmd-worker.ps1"
        ).read_text(encoding="utf-8")
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
        embedded_genome = research_dashboard._genome_payload()
        if any(embedded_genome.get(key) != value for key, value in expected_genome.items()):
            raise SystemExit("failed: embedded dashboard did not resolve canonical Genome artifact")
        research_dashboard.ROOT = agent_root
        research_dashboard.DATA_ROOT = agent_root
        research_dashboard._API_RESPONSE_CACHE.clear()
        standalone_genome = research_dashboard._genome_payload()
        if any(standalone_genome.get(key) != value for key, value in expected_genome.items()):
            raise SystemExit("failed: standalone dashboard did not resolve canonical Genome artifact")
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
    health_response = client.get("/api/health")
    if health_response.status_code != 200:
        raise SystemExit(f"failed: /api/health returned {health_response.status_code}")
    health_payload = health_response.get_json()
    if not health_payload.get("ok") or not health_payload.get("report_root"):
        raise SystemExit("failed: /api/health did not expose live canonical identity")
    response = client.get("/api/status")
    if response.status_code != 200:
        raise SystemExit(f"failed: /api/status returned {response.status_code}")
    payload = response.get_json()
    if payload.get("runtime_analyzer_sync_id") != payload.get("expected_analyzer_sync_id"):
        raise SystemExit("failed: runtime analyzer identity does not match expected identity")

print(f"PASS: {len(checks)} analyzer startup-health checks + live /api/status")
