from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SCRIPT = (REPO / "scripts" / "sync-platform-relay-evidence.ps1").read_text(encoding="utf-8")
CONTROLLER = (REPO / "apps" / "api" / "src" / "trading-agents" / "trading-agents.controller.ts").read_text(encoding="utf-8")
SERVICE = (REPO / "apps" / "api" / "src" / "trading-agents" / "trading-agents.service.ts").read_text(encoding="utf-8")


def test_export_is_authenticated_user_scoped_and_event_complete():
    assert "ops/relay-evidence" in CONTROLLER
    assert "X-Bot-Admin-Token" in CONTROLLER
    assert "await this.getOpsRelayStatus" in SERVICE
    assert "canonicalTradeId" in SERVICE
    assert "eventType: event.eventType" in SERVICE
    assert "generatingRevision" in SERVICE and "runIdentity" in SERVICE


def test_sync_is_atomic_and_fails_closed_on_missing_provenance():
    assert "Invoke-RestMethod" in SCRIPT
    assert "relay_lifecycle_evidence_v1" in SCRIPT
    assert "generatingRevision" in SCRIPT and "runIdentity" in SCRIPT
    assert "Move-Item -LiteralPath $temp" in SCRIPT
    assert "throw 'Relay evidence provenance is incomplete" in SCRIPT


def test_sync_never_accepts_or_outputs_token_on_command_line():
    assert "[string]$AdminToken" not in SCRIPT
    assert "GetEnvironmentVariable('BOT_ADMIN_TOKEN', 'Process')" in SCRIPT
    assert "Write-Output $adminToken" not in SCRIPT
    assert "ConvertTo-Json" in SCRIPT and "$payload" in SCRIPT
