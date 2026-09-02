from pathlib import Path
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start-fly-desktop-mirror.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_reload_requires_exact_revision_pid_and_terminal_heartbeat_identity():
    text = source()
    for parameter in (
        "ReloadFailedSyncOwner",
        "ExpectedSyncPid",
        "ExpectedSyncCreationUtc",
        "ExpectedHeartbeatSha256",
        "ExpectedPollFailedAt",
        "ExpectedClientRevision",
        "ExpectedBootstrapReceiptPath",
        "ExpectedBootstrapReceiptSha256",
        "ExpectedDeployedRevision",
        "ExpectedEpochId",
        "ExpectedConfigSignature",
        "ExpectedRuntimeTreeSha256",
    ):
        assert f"${parameter}" in text
    assert "rev-parse HEAD" in text
    assert "^[0-9a-fA-F]{40}$" in text
    assert "^[0-9a-fA-F]{64}$" in text
    assert "pollFailedAt -cne $PollFailedAt" in text
    assert "pollStage -notin @('loop_full_manifest', 'loop_manifest_preflight')" in text
    assert "$nextRetry -le [DateTimeOffset]::UtcNow.AddSeconds(5)" in text
    assert "minimum five-second terminal backoff fence" in text
    assert "fly_sync_reload_bootstrap_authorization_v1" in text
    assert "bootstrap.complete -ne $true" in text
    assert "bootstrap.blocked -eq $true" in text
    assert "pending_orders -ne 0" in text
    assert "open_positions -ne 0" in text
    assert "local client/analyzer HEAD does not equal the deployed revision" in text
    assert "tracked working tree is dirty; analyzer provenance would be false" in text


def test_reload_is_single_owner_command_bound_and_rechecks_before_stop():
    text = source()
    helper = text[text.index("function Get-ExactSyncOwnerProcess") :]
    assert "$owners.Count -ne 1" in helper
    assert "-File\\s+" in helper
    assert "Win32_Process" in helper
    assert "ParentProcessId=$ProcessId" in helper
    assert "-notmatch '^conhost\\.exe$'" in helper
    assert "$Owner.CreationDate -is [datetime]" in helper
    reload_block = text[text.index("if ($ReloadFailedSyncOwner)") :]
    assert reload_block.count("Assert-TerminalSyncReloadProof") == 2
    assert reload_block.index("Assert-TerminalSyncReloadProof") < reload_block.index("Stop-Process")
    assert "$stopTarget = Get-Process -Id $ExpectedSyncPid" in reload_block
    assert "$stopTarget.Kill()" in reload_block
    assert "opened stop target is not the proven owner instance" in reload_block
    assert "ExpectedCreationUtc" in helper
    assert "taskkill" not in reload_block.lower()


def test_reload_preserves_terminal_receipt_and_reuses_atomic_loop_locks():
    text = source()
    reload_block = text[text.index("if ($ReloadFailedSyncOwner)") :]
    assert "logs\\sync-owner-reloads" in reload_block
    assert "New-Item -ItemType Directory -Path $receiptDir -Force -ErrorAction Stop" in reload_block
    assert '"terminal-$($proof.Hash).json"' in reload_block
    assert "$terminalBytes = [IO.File]::ReadAllBytes($syncHeartbeat)" in reload_block
    stop = reload_block.index("$stopTarget.Kill()")
    copy = reload_block.index("[IO.File]::ReadAllBytes($syncHeartbeat)")
    retained = reload_block.index("terminal heartbeat receipt was not durably retained")
    rehash = reload_block.rindex("Get-Sha256Hex -Path $receiptPath", 0, stop)
    assert copy < rehash < retained < stop
    assert "Remove-Item -LiteralPath $syncLock,$syncHeartbeat" in reload_block
    assert "sync-fly-bot-data-loop.ps1" in text
    assert "Get-ExactSyncOwnerProcess -ProcessId $newPid" in reload_block
    assert "fly_desktop_sync_owner_reload_v1" in reload_block
    assert "terminal_heartbeat_sha256 = $proof.Hash" in reload_block
    assert "old_pid = $ExpectedSyncPid" in reload_block
    assert "new_pid = $newPid" in reload_block
    assert "Write-ImmutableBytes -Path $receiptPath" in reload_block
    assert "[IO.FileOptions]::WriteThrough" in text
    assert "$stream.Flush($true)" in text
    assert "-PassThru" in reload_block
    assert "$newPid -ne [int]$spawned.Id" in reload_block
    assert "runtime_tree_sha256 = $runtimeProof.Hash" in reload_block
    assert "runtime_files = $runtimeProof.Files" in reload_block
    assert "bootstrap_receipt_sha256 = $bootstrapProof.Hash" in reload_block
    assert "old_process_creation_utc = $proof.CreationUtc" in reload_block
    assert "new_process_creation_utc = $newCreationUtc" in reload_block
    assert "Move-Item -LiteralPath $temporary -Destination $Path -ErrorAction Stop" in text
    assert "Move-Item -LiteralPath $reloadTemporary -Destination $reloadReceipt -Force" not in text
    assert reload_block.index("return") < text.index("# Stop only the former desktop production runtime")


def test_runtime_proof_covers_sync_and_analyzer_execution_closure():
    text = source()
    for relative in (
        "scripts/start-fly-desktop-mirror.ps1",
        "scripts/sync-fly-bot-data-loop.ps1",
        "scripts/sync-fly-bot-data.ps1",
        "scripts/fly-data-paths.ps1",
        "scripts/fly-canonical-lock.ps1",
        "scripts/start-home-analyzer.ps1",
        "scripts/research-stability-supervisor.py",
        "services/btc-conservative-agent/analyzer_research_engine_v62.py",
        "services/btc-conservative-agent/research_v3_store.py",
    ):
        assert f"'{relative}'" in text


def test_invalid_bootstrap_proof_refuses_before_any_takeover(tmp_path):
    """Run a sandbox copy and prove a bad receipt cannot reach process takeover."""
    sandbox = tmp_path / "repo"
    scripts = sandbox / "scripts"
    scripts.mkdir(parents=True)
    candidate = scripts / SCRIPT.name
    candidate.write_text(source(), encoding="utf-8")
    (scripts / "fly-canonical-lock.ps1").write_text(
        "function Get-CanonicalFlyBotUrl { param([string]$RequestedUrl); return $RequestedUrl }\n",
        encoding="utf-8",
    )
    (scripts / "fly-data-paths.ps1").write_text(
        "function Get-DoxxedFlyMirrorDir { return (Join-Path -Parent $PSScriptRoot 'mirror') }\n",
        encoding="utf-8",
    )
    # The fake git command reaches the receipt check only after satisfying clean
    # revision checks. Missing runtime files are intentionally earlier than any
    # process mutation too, so use an invalid parameter proof to test the first
    # fail-closed boundary without touching the host process table.
    marker = sandbox / "takeover-marker"
    command = (
        f"$ErrorActionPreference='Stop'; "
        f"try {{ & '{str(candidate).replace(chr(39), chr(39) * 2)}' "
        "-ReloadFailedSyncOwner -ExpectedSyncPid 99 } catch { "
        f"if(Test-Path -LiteralPath '{str(marker).replace(chr(39), chr(39) * 2)}'){{exit 9}}; "
        "if($_.Exception.Message -notmatch 'exact process, heartbeat, bootstrap, deployment, and runtime provenance')"
        "{ Write-Error $_; exit 8 }; exit 0 }; exit 7"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_sandboxed_mock_takeover_binds_spawned_pid_and_writes_immutable_receipts(tmp_path):
    """Exercise the complete takeover path with process cmdlets mocked in a sandbox."""
    sandbox = tmp_path / "repo"
    scripts = sandbox / "scripts"
    agent = sandbox / "services" / "btc-conservative-agent"
    mirror = sandbox / "mirror"
    scripts.mkdir(parents=True)
    agent.mkdir(parents=True)
    mirror.mkdir()
    runtime_paths = (
        "scripts/start-fly-desktop-mirror.ps1",
        "scripts/sync-fly-bot-data-loop.ps1",
        "scripts/sync-fly-bot-data.ps1",
        "scripts/fly-data-paths.ps1",
        "scripts/fly-canonical-lock.ps1",
        "scripts/start-home-analyzer.ps1",
        "scripts/research-stability-supervisor.py",
        "services/btc-conservative-agent/analyzer_research_engine_v62.py",
        "services/btc-conservative-agent/research_v3_store.py",
    )
    for relative in runtime_paths:
        target = sandbox / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative == runtime_paths[0]:
            target.write_text(source(), encoding="utf-8")
        elif relative == "scripts/fly-canonical-lock.ps1":
            target.write_text(
                "function Get-CanonicalFlyBotUrl { param([string]$RequestedUrl); return $RequestedUrl }\n",
                encoding="utf-8",
            )
        elif relative == "scripts/fly-data-paths.ps1":
            escaped = str(mirror).replace("'", "''")
            target.write_text(f"function Get-DoxxedFlyMirrorDir {{ return '{escaped}' }}\n", encoding="utf-8")
        else:
            target.write_text(f"# sandbox {relative}\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=sandbox, check=True)
    subprocess.run(["git", "config", "user.email", "sandbox@example.invalid"], cwd=sandbox, check=True)
    subprocess.run(["git", "config", "user.name", "Sandbox"], cwd=sandbox, check=True)
    subprocess.run(["git", "add", "."], cwd=sandbox, check=True)
    subprocess.run(["git", "commit", "-qm", "sandbox"], cwd=sandbox, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=sandbox, text=True).strip()
    tree_lines = []
    for relative in runtime_paths:
        digest = hashlib.sha256((sandbox / relative).read_bytes()).hexdigest()
        tree_lines.append(f"{relative}\t{digest}")
    tree_hash = hashlib.sha256(("\n".join(tree_lines) + "\n").encode()).hexdigest()

    now = datetime.now(timezone.utc)
    created = now - timedelta(minutes=3)
    failed = now - timedelta(minutes=2)
    heartbeat = {
        "ok": False,
        "pollOk": False,
        "inProgress": False,
        "pollStage": "loop_full_manifest",
        "pollError": "sandbox terminal failure",
        "pollFailedAt": failed.isoformat().replace("+00:00", "Z"),
        "nextRetryAt": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
    }
    heartbeat_path = mirror / ".fly-data-sync-loop.heartbeat.json"
    heartbeat_path.write_text(json.dumps(heartbeat), encoding="utf-8")
    heartbeat_hash = hashlib.sha256(heartbeat_path.read_bytes()).hexdigest()
    (sandbox / ".fly-data-sync-loop.lock").write_text("41001", encoding="utf-8")
    bootstrap = {
        "schema": "fly_sync_reload_bootstrap_authorization_v1",
        "captured_at": now.isoformat().replace("+00:00", "Z"),
        "workflow_run_id": "sandbox-run",
        "source_revision": revision,
        "epoch_id": "epoch-sandbox",
        "config_signature": "config-sandbox",
        "inventory_status": "CURRENT",
        "receipt_bootstrap": {"status": "COMPLETE", "complete": True, "blocked": False},
        "paper_only": True,
        "bitfinex_live_enabled": False,
        "live_armed": False,
        "pending_orders": 0,
        "open_positions": 0,
    }
    bootstrap_path = sandbox / "bootstrap.json"
    bootstrap_path.write_text(json.dumps(bootstrap), encoding="utf-8")
    bootstrap_hash = hashlib.sha256(bootstrap_path.read_bytes()).hexdigest()
    sync_path = scripts / "sync-fly-bot-data-loop.ps1"
    created_ps = created.isoformat().replace("+00:00", "Z")
    failed_ps = heartbeat["pollFailedAt"]
    script_path = scripts / SCRIPT.name
    q = lambda value: str(value).replace("'", "''")
    harness = sandbox / "harness.ps1"
    harness.write_text(
        f"""
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
$global:oldAlive = $true
$global:newAlive = $false
$global:oldCreated = [DateTimeOffset]::Parse('{created_ps}').UtcDateTime
$global:newCreated = [DateTimeOffset]::UtcNow.UtcDateTime
$global:syncPath = '{q(sync_path)}'
$global:lockPath = '{q(sandbox / '.fly-data-sync-loop.lock')}'
function global:Get-CimInstance {{
  param([string]$ClassName, [string]$Filter, $ErrorAction)
  if ($Filter) {{ return @() }}
  if ($global:oldAlive) {{
    return [pscustomobject]@{{ ProcessId=41001; ParentProcessId=1; Name='powershell.exe'; CreationDate=$global:oldCreated; CommandLine=('powershell -File "' + $global:syncPath + '"') }}
  }}
  if ($global:newAlive) {{
    return [pscustomobject]@{{ ProcessId=42002; ParentProcessId=1; Name='powershell.exe'; CreationDate=$global:newCreated; CommandLine=('powershell -File "' + $global:syncPath + '"') }}
  }}
  return @()
}}
function global:Get-Process {{
  param([int]$Id, $ErrorAction)
  if ($Id -eq 41001 -and $global:oldAlive) {{
    $row = [pscustomobject]@{{ Id=41001; StartTime=$global:oldCreated }}
    $row | Add-Member ScriptMethod Kill {{ $global:oldAlive = $false }}
    return $row
  }}
  if ($Id -eq 42002 -and $global:newAlive) {{ return [pscustomobject]@{{ Id=42002; StartTime=$global:newCreated }} }}
  return $null
}}
function global:Start-Process {{
  param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
  $global:newCreated = [DateTimeOffset]::UtcNow.UtcDateTime
  $global:newAlive = $true
  [IO.File]::WriteAllText($global:lockPath, '42002')
  return [pscustomobject]@{{ Id=42002 }}
}}
& '{q(script_path)}' -ReloadFailedSyncOwner `
  -ExpectedSyncPid 41001 -ExpectedSyncCreationUtc '{created_ps}' `
  -ExpectedHeartbeatSha256 '{heartbeat_hash}' -ExpectedPollFailedAt '{failed_ps}' `
  -ExpectedClientRevision '{revision}' -ExpectedBootstrapReceiptPath '{q(bootstrap_path)}' `
  -ExpectedBootstrapReceiptSha256 '{bootstrap_hash}' -ExpectedDeployedRevision '{revision}' `
  -ExpectedEpochId 'epoch-sandbox' -ExpectedConfigSignature 'config-sandbox' `
  -ExpectedRuntimeTreeSha256 '{tree_hash}'
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        cwd=sandbox,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipts = mirror / "logs" / "sync-owner-reloads"
    reload_receipt = json.loads(next(receipts.glob("reload-*.json")).read_text(encoding="utf-8-sig"))
    assert reload_receipt["old_pid"] == 41001
    assert reload_receipt["new_pid"] == 42002
    assert reload_receipt["terminal_heartbeat_sha256"] == heartbeat_hash
    assert reload_receipt["runtime_tree_sha256"] == tree_hash
    assert reload_receipt["bootstrap_receipt_sha256"] == bootstrap_hash
    assert (sandbox / ".fly-data-sync-loop.lock").read_text() == "42002"
    assert hashlib.sha256(next(receipts.glob("terminal-*.json")).read_bytes()).hexdigest() == heartbeat_hash


def test_script_parses_in_windows_powershell():
    command = (
        "$errors=$null; [void][System.Management.Automation.Language.Parser]::"
        f"ParseFile('{str(SCRIPT).replace(chr(39), chr(39) * 2)}',[ref]$null,[ref]$errors); "
        "if($errors.Count){$errors | ForEach-Object {$_.ToString()}; exit 1}"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
