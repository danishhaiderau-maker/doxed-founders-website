"""Exercise terminal ACK evidence without contacting Fly."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


SCRIPTS = Path(__file__).resolve().parent


def _ps_literal(value: str) -> str:
    return value.replace("'", "''")


def _child_function_source() -> str:
    source = (SCRIPTS / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    start = source.index("function Get-DataSyncManifestIdentityValue")
    end = source.index("$syncState = @{}", start)
    return source[start:end]


def _transport_function_source() -> str:
    source = (SCRIPTS / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    start = source.index("function ConvertTo-DataSyncCanonicalInventoryTimestamp")
    end = source.index("function New-DataSyncManifestUri", start)
    return source[start:end]


def _validator_function_source() -> str:
    source = (SCRIPTS / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    start = source.index("function Test-DataSyncTerminalAcknowledgementCount")
    end = source.index("function New-DataSyncTerminalAcknowledgement", start)
    return source[start:end]


def _assert_source_order() -> None:
    source = (SCRIPTS / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    ack_call = source.index('$ack = Invoke-DataSyncJsonRequest')
    raw_gate = source.index("Assert-DataSyncRawFinalizeAcknowledgement", ack_call)
    post_ack = source.index(
        "Assert-DataSyncManifestIdentity -Initial $manifest -Final $postAckManifest",
        raw_gate,
    )
    receipt_build = source.index(
        "$terminalMembershipEvidence = New-DataSyncTerminalMembershipReceipt",
        post_ack,
    )
    receipt_write = source.index(
        "$terminalMembershipReceipt = Write-DataSyncTerminalMembershipReceipt",
        receipt_build,
    )
    private_heartbeat = source.index(
        "$canonicalCandidate = if ($ProgressHeartbeatFile)", receipt_write
    )
    migration = source.index(
        "$canonicalManifestReceipt = & python $migrationScript", private_heartbeat
    )
    public_publish = source.index(
        "-Destination $ProgressHeartbeatFile", migration
    )
    assert ack_call < raw_gate < post_ack < receipt_build < receipt_write
    assert receipt_write < private_heartbeat < migration < public_publish
    assert "-ReceiptTarget $canonicalCandidate" in source[private_heartbeat:migration]
    assert "-TerminalAcknowledgement $terminalAcknowledgement" in source[private_heartbeat:migration]


def _run_powershell(harness: str, *, timeout: int = 45) -> str:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("PowerShell is required for sync ACK contract checks")
    result = subprocess.run(
        [shell, "-NoProfile", "-Command", harness],
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


def _run() -> str:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("PowerShell is required for sync ACK contract checks")
    with tempfile.TemporaryDirectory(prefix="btc-sync-ack-contract-") as temp:
        root = _ps_literal(temp)
        harness = f"""
$ErrorActionPreference='Stop'
function Assert-FlyBundleUnlinkedPath {{ param([string]$Path) }}
function Resolve-DataSyncManifestMemberPath {{
  param([string]$Root,[string]$MemberPath)
  return [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetFullPath($Root)) ($MemberPath -replace '/', '\\')))
}}
$functionSource=@'
{_child_function_source()}
'@
Invoke-Expression $functionSource

$targetRoot='{root}'
[IO.File]::WriteAllText((Join-Path $targetRoot 'sample.jsonl'),'abc')
$generation=('a'*64)
$canonical=('b'*40)
$manifest=[pscustomobject]@{{
  inventory_generation_id=$generation; inventory_sha256=$generation;
  inventory_generated_at='2026-09-14T00:00:00.0000000+00:00';
  source_git_rev=$canonical.Substring(0,12); collection_epoch_id='epoch-test';
  tile_registry_signature=('c'*64); file_count=[long]1; total_bytes=[long]3;
  manifest_page_count=[int]1;
  manifest_page_receipts=@([pscustomobject]@{{
    page_index=[int]0; page_sha256=('d'*64); file_count=[int]1; total_bytes=[long]3
  }})
}}
$finalAck=[pscustomobject]@{{
  ok=$true; operation='FINALIZE'; inventory_status='VALIDATED';
  inventory_generation_id=$generation; inventory_sha256=$generation;
  inventory_generated_at=$manifest.inventory_generated_at; accepted=[long]1;
  rejected_count=[long]0; inventory_file_count=[long]1;
  manifest_page_count=[int]1; manifest_pages_complete=$true; ack_session_id=('e'*32)
}}
$membership=New-DataSyncTerminalMembershipReceipt -Manifest $manifest -FinalAck $finalAck `
  -SelectedFiles @([pscustomobject]@{{path='sample.jsonl';size=[long]3}}) `
  -TargetRoot $targetRoot -AckExpectedCount 1 -AckAcceptedCount 1 `
  -AckRejectedCount 0 -AckSessionId ('e'*32) -CanonicalSourceRevision $canonical `
  -PostAckIdentityFencePassed
$persisted=Write-DataSyncTerminalMembershipReceipt -TargetRoot $targetRoot -Receipt $membership
$summary=New-DataSyncTerminalAcknowledgement -Manifest $manifest -FinalAck $finalAck `
  -MembershipReceipt $membership -PersistedMembershipReceipt $persisted `
  -AckExpectedCount 1 -AckAcceptedCount 1 -AckRejectedCount 0 `
  -AckSessionId ('e'*32) -CanonicalSourceRevision $canonical

$wrongSessionAck=[pscustomobject]@{{}}
foreach($property in $finalAck.PSObject.Properties) {{
  $wrongSessionAck | Add-Member -NotePropertyName $property.Name -NotePropertyValue $property.Value
}}
$wrongSessionAck.ack_session_id=('f'*32)
$wrongSessionRejected=$false
try {{
  New-DataSyncTerminalMembershipReceipt -Manifest $manifest -FinalAck $wrongSessionAck `
    -SelectedFiles @([pscustomobject]@{{path='sample.jsonl';size=[long]3}}) `
    -TargetRoot $targetRoot -AckExpectedCount 1 -AckAcceptedCount 1 `
    -AckRejectedCount 0 -AckSessionId ('e'*32) -CanonicalSourceRevision $canonical `
    -PostAckIdentityFencePassed | Out-Null
}} catch {{ $wrongSessionRejected=$true }}
if(-not $wrongSessionRejected) {{ throw 'WRONG_FINALIZE_SESSION_WAS_ACCEPTED' }}

$missingSessionAck=[pscustomobject]@{{}}
foreach($property in $finalAck.PSObject.Properties) {{
  if($property.Name -cne 'ack_session_id') {{
    $missingSessionAck | Add-Member -NotePropertyName $property.Name -NotePropertyValue $property.Value
  }}
}}
$missingSessionRejected=$false
try {{
  New-DataSyncTerminalMembershipReceipt -Manifest $manifest -FinalAck $missingSessionAck `
    -SelectedFiles @([pscustomobject]@{{path='sample.jsonl';size=[long]3}}) `
    -TargetRoot $targetRoot -AckExpectedCount 1 -AckAcceptedCount 1 `
    -AckRejectedCount 0 -AckSessionId ('e'*32) -CanonicalSourceRevision $canonical `
    -PostAckIdentityFencePassed | Out-Null
}} catch {{ $missingSessionRejected=$true }}
if(-not $missingSessionRejected) {{ throw 'MISSING_FINALIZE_SESSION_WAS_ACCEPTED' }}

$rejectedFinal=[pscustomobject]@{{
  ok=$true; operation='FINALIZE'; inventory_status='VALIDATED';
  inventory_generation_id=$generation; inventory_sha256=$generation;
  inventory_generated_at=$manifest.inventory_generated_at; accepted=[long]1;
  rejected_count=[long]1; inventory_file_count=[long]1;
  manifest_page_count=[int]1; manifest_pages_complete=$true; ack_session_id=('e'*32)
}}
$remoteRejected=$false
try {{
  New-DataSyncTerminalAcknowledgement -Manifest $manifest -FinalAck $rejectedFinal `
    -MembershipReceipt $membership -PersistedMembershipReceipt $persisted `
    -AckExpectedCount 1 -AckAcceptedCount 1 -AckRejectedCount 0 `
    -AckSessionId ('e'*32) -CanonicalSourceRevision $canonical | Out-Null
}} catch {{ $remoteRejected=$true }}
if(-not $remoteRejected) {{ throw 'REJECTED_FINALIZE_WAS_ACCEPTED' }}

# Raw FINALIZE values must be rejected before a new membership artifact can
# be persisted. In particular PowerShell's ordinary casts must not turn JSON
# strings such as "1" or "true" into valid terminal authority.
$receiptDirectory=Join-Path $targetRoot 'receipts\terminal-transfer-membership'
$baselineReceiptCount=@(Get-ChildItem -LiteralPath $receiptDirectory -File -ErrorAction SilentlyContinue).Count
$rawCases=@(
  @{{Name='accepted';Value='1'}},
  @{{Name='rejected_count';Value='0'}},
  @{{Name='inventory_file_count';Value='1'}},
  @{{Name='manifest_page_count';Value='1'}},
  @{{Name='ok';Value='true'}},
  @{{Name='manifest_pages_complete';Value=1}}
)
foreach($rawCase in $rawCases) {{
  $badRaw=[pscustomobject]@{{}}
  foreach($property in $finalAck.PSObject.Properties) {{
    $badRaw | Add-Member -NotePropertyName $property.Name -NotePropertyValue $property.Value
  }}
  $badRaw.($rawCase.Name)=$rawCase.Value
  $rawRejected=$false
  try {{
    Assert-DataSyncRawFinalizeAcknowledgement -Manifest $manifest -FinalAck $badRaw -AckSessionId ('e'*32)
    $unexpectedMembership=New-DataSyncTerminalMembershipReceipt -Manifest $manifest -FinalAck $badRaw `
      -SelectedFiles @([pscustomobject]@{{path='sample.jsonl';size=[long]3}}) `
      -TargetRoot $targetRoot -AckExpectedCount 1 -AckAcceptedCount 1 `
      -AckRejectedCount 0 -AckSessionId ('e'*32) -CanonicalSourceRevision $canonical `
      -PostAckIdentityFencePassed
    Write-DataSyncTerminalMembershipReceipt -TargetRoot $targetRoot -Receipt $unexpectedMembership | Out-Null
  }} catch {{
    $rawRejected=($_.Exception.Message -ceq 'Raw FINALIZE acknowledgement fields are invalid.')
  }}
  if(-not $rawRejected) {{ throw "RAW_FINALIZE_COERCION_ACCEPTED_$($rawCase.Name)" }}
  if(@(Get-ChildItem -LiteralPath $receiptDirectory -File -ErrorAction SilentlyContinue).Count -ne $baselineReceiptCount) {{
    throw "RAW_FINALIZE_MEMBERSHIP_PERSISTED_$($rawCase.Name)"
  }}
}}

Write-Output 'SYNC_ACK_RESULT_CONTRACT_OK'
"""
        harness_path = Path(temp) / "contract.ps1"
        harness_path.write_text(harness, encoding="utf-8")
        result = subprocess.run(
            [shell, "-NoProfile", "-File", str(harness_path)],
            text=True,
            capture_output=True,
            timeout=45,
        )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


def _run_invoke_rest_method_timestamp_case(timestamp: str, *, valid: bool) -> None:
    body = (
        '{"inventory_generated_at":"' + timestamp + '",'
        '"ok":true,"manifest_pages_complete":true,"operation":"FINALIZE",'
        '"inventory_status":"VALIDATED","inventory_generation_id":"' + ("a" * 64) + '",'
        '"inventory_sha256":"' + ("a" * 64) + '","ack_session_id":"' + ("d" * 32) + '",'
        '"accepted":1,"rejected_count":0,"inventory_file_count":1,"manifest_page_count":1}'
    ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="btc-sync-irm-contract-") as temp:
            marker = _ps_literal(str(Path(temp) / "FINALIZE_VALIDATED.json"))
            url = f"http://127.0.0.1:{server.server_port}/finalize"
            expected = "$true" if valid else "$false"
            harness = f"""
$ErrorActionPreference='Stop'
$manifestTimeoutSec=5
$transportAttempts=1
$headers=@{{}}
function Test-DataSyncResourcePressureError {{param([string]$Message) return $false}}
function Get-DataSyncRetryDelaySec {{param([int]$Attempt,[bool]$ResourcePressure) return 1}}
{_transport_function_source()}
{_validator_function_source()}
$raw=Invoke-RestMethod -Uri '{url}' -Method Get
$expectedValid={expected}
if($expectedValid -and $raw.inventory_generated_at -isnot [DateTime]) {{throw 'VALID_IRM_TIMESTAMP_SHAPE_UNEXPECTED'}}
if(-not $expectedValid -and $raw.inventory_generated_at -isnot [string]) {{throw 'INVALID_IRM_TIMESTAMP_SHAPE_UNEXPECTED'}}
$caught=$false
try {{
  $ack=Invoke-DataSyncJsonRequest -Stage 'local_finalize_mock' -Uri '{url}' -Method Get -MaxAttempts 1 -MaxElapsedSec 10
  $manifest=[pscustomobject]@{{file_count=[long]1;manifest_page_count=[long]1;
    inventory_generation_id=('a'*64);inventory_sha256=('a'*64);
    inventory_generated_at='2026-09-14T00:00:00.123456+00:00'}}
  Assert-DataSyncRawFinalizeAcknowledgement -Manifest $manifest -FinalAck $ack -AckSessionId ('d'*32)
  Set-Content -LiteralPath '{marker}' -Value 'FINALIZE_VALIDATED'
}} catch {{
  if($expectedValid) {{throw}}
  $caught=$true
}}
if($caught -eq $expectedValid) {{throw 'TIMESTAMP_GATE_OUTCOME_INVALID'}}
if($expectedValid -and -not (Test-Path -LiteralPath '{marker}')) {{throw 'VALID_FINALIZE_NOT_PERSISTED'}}
if(-not $expectedValid -and (Test-Path -LiteralPath '{marker}')) {{throw 'INVALID_FINALIZE_PERSISTED'}}
Write-Output 'IRM_TIMESTAMP_CONTRACT_OK'
"""
            output = _run_powershell(harness, timeout=30)
            if not output.endswith("IRM_TIMESTAMP_CONTRACT_OK"):
                raise RuntimeError(f"unexpected Invoke-RestMethod contract output: {output!r}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    _assert_source_order()
    output = _run()
    if output != "SYNC_ACK_RESULT_CONTRACT_OK":
        raise SystemExit(f"unexpected contract output: {output!r}")
    _run_invoke_rest_method_timestamp_case(
        "2026-09-14T00:00:00.123456+00:00", valid=True
    )
    _run_invoke_rest_method_timestamp_case("not-a-timestamp", valid=False)
    print(output)
