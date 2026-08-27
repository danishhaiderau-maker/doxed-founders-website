from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def source(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8-sig")


def test_health_contract_separates_liveness_from_readiness() -> None:
    health = source("home-stack-health.ps1")

    assert "function Get-AnalyzerRuntimeStatus" in health
    assert "function Test-AnalyzerAlive" in health
    assert "function Test-AnalyzerHealthy" in health
    assert "$result.Alive = ($s.alive -eq $true)" in health
    assert "$s.ok -eq $true" in health
    assert "$s.ready -eq $true" in health
    assert "if (Test-AnalyzerAlive) { return $false }" in health


def test_mutating_callers_recover_only_on_liveness_failure() -> None:
    supervisor = source("home-stack-supervisor.ps1")
    local_supervisor = source("home-stack-supervisor-local.ps1")
    overnight = source("overnight-architecture-guard.ps1")
    worker = source("home-stack-cmd-worker.ps1")
    collection = source("home-stack-start-collection.ps1")

    assert "$analyzerAlive = Test-AnalyzerAlive" in supervisor
    assert "if ($analyzerAlive) { $fail.analyzer = 0 } else { $fail.analyzer++ }" in supervisor
    assert "if (-not (Test-AnalyzerAlive))" in local_supervisor
    assert "$analyzerAlive = Test-AnalyzerAlive" in overnight
    assert "if ($analyzerAlive) { $fail.analyzer = 0 } else { $fail.analyzer++ }" in overnight
    assert '"start-analyzer"' in worker and "if (-not (Test-AnalyzerAlive))" in worker
    assert "if (-not (Test-AnalyzerAlive))" in collection


def test_launchers_preserve_alive_dashboard_but_still_report_readiness() -> None:
    launcher = source("start-home-analyzer.ps1")
    everything = source("home-stack-start-everything.ps1")

    assert "$dashboardAlive = (Test-AnalyzerAlive)" in launcher
    assert "$dashboardReady = (Test-AnalyzerHealthy)" in launcher
    assert "$dashboardAlive -and $listenerPids.Count -eq 1 -and $engineAlive" in launcher
    assert "if (-not (Test-AnalyzerAlive))" in everything
    assert "if (Test-AnalyzerHealthy) { break }" in everything
    assert "Analyzer FAILED verification" in everything
