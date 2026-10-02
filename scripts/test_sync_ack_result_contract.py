"""Direct child-to-resume ACK contract checks without network or pytest.

The child helper constructs the exact result consumed by
``Invoke-FlyGenerationResume``.  It is intentionally exercised from extracted
production functions so a numeric accepted-file count cannot masquerade as a
Boolean remote FINALIZE result.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile


SCRIPTS = Path(__file__).resolve().parent


def _ps_literal(value: str) -> str:
    return value.replace("'", "''")


def _child_function_source() -> str:
    source = (SCRIPTS / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    start = source.index("function Get-DataSyncManifestIdentityValue")
    end = source.index("$syncState = @{}", start)
    return source[start:end]


def _run() -> str:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("PowerShell is required for sync ACK contract checks")

    resume = _ps_literal(str(SCRIPTS / "fly-sync-generation-resume.ps1"))
    with tempfile.TemporaryDirectory(prefix="btc-sync-ack-contract-") as temp:
        root = _ps_literal(temp)
        failure_path = _ps_literal(str(Path(temp) / "terminal-failure.json"))
        harness = f"""
$ErrorActionPreference='Stop'
function Assert-FlyBundleUnlinkedPath {{ param([string]$Path) }}
$functionSource=@'
{_child_function_source()}
'@
Invoke-Expression $functionSource
. '{resume}'

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

$resumeManifest=[pscustomobject]@{{
  inventory_generation_id=$generation; inventory_sha256=$generation;
  source_git_rev=$canonical; collection_epoch_id='epoch-test';
  tile_registry_signature=('c'*64); file_count=[long]1; total_bytes=[long]3;
  inventory_status='CURRENT'; inventory_authoritative=$true; inventory_ack_eligible=$true
}}
$identity=@{{inventory_generation_id=$generation;inventory_sha256=$generation;source_git_rev=$canonical;collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$read={{param($ignored) return $resumeManifest}}.GetNewClosure()
$run={{param($ignoredManifest,$attempt) return @{{Success=$true;Result=$summary}}}}.GetNewClosure()
$accepted=Invoke-FlyGenerationResume -Identity $identity -ReadManifest $read -RunAttempt $run
if($accepted.AckAccepted -ne $true -or $accepted.AckAcceptedCount -ne 1 -or
   $accepted.AckMembershipReceiptSha256 -cnotmatch '^[0-9a-f]{{64}}$' -or
   $accepted.AckLocalContentDigestSha256 -cnotmatch '^[0-9a-f]{{64}}$') {{
  throw 'CHILD_TO_RESUME_SUCCESS_CONTRACT_FAILED'
}}

# A successful transport count is not a terminal Boolean result and cannot be
# accepted when its durable local coverage proof is absent.
$bad=[ordered]@{{}}
foreach($property in $summary.PSObject.Properties) {{ $bad[$property.Name]=$property.Value }}
$bad.AckMembershipReceiptSha256=''
$badResult=[pscustomobject]$bad
$badRun={{param($ignoredManifest,$attempt) return @{{Success=$true;Result=$badResult}}}}.GetNewClosure()
$badRejected=$false
try {{ Invoke-FlyGenerationResume -Identity $identity -ReadManifest $read -RunAttempt $badRun | Out-Null }}
catch {{ $badRejected=($_.Exception.Message -ceq 'RESUME_TERMINAL_ACK_INVALID') }}
if(-not $badRejected) {{ throw 'COVERAGELESS_ACK_WAS_ACCEPTED' }}

# FINALIZE is only valid for the exact ACK session that made the page receipts.
# A delayed response from an earlier session must not certify this transfer.
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

# The child cannot emit AckAccepted when the remote FINALIZE reports a rejected
# member, even though the local receipt could otherwise look complete.
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

# Terminal errors retain bounded classification/digest metadata, never raw
# response content or a secret-like payload.
$secret='do-not-persist-this-secret'
try {{
  throw "Fly data-sync stage=acknowledgement_finalize failed: HTTP 409 identity mismatch $secret"
}} catch {{
  $failure=New-FlyGenerationResumeFailureReceipt -ErrorRecord $_ -Identity $identity -Manifest $resumeManifest -Attempt 1
  Write-FlyGenerationResumeFailureReceipt -Path '{failure_path}' -Receipt $failure
}}
$stored=[IO.File]::ReadAllText('{failure_path}')
if($failure.ok -ne $false -or $failure.inProgress -ne $false -or
   $failure.phase -cne 'terminal_failure' -or
   $failure.failureCode -cne 'ACK_HTTP_409_IDENTITY_MISMATCH' -or
   $failure.failureDiagnostic.responseExcerptSha256 -cnotmatch '^[0-9a-f]{{64}}$' -or
   $stored.Contains($secret) -or $stored.Contains('identity mismatch')) {{
  throw 'TERMINAL_FAILURE_REDACTION_FAILED'
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


if __name__ == "__main__":
    output = _run()
    if output != "SYNC_ACK_RESULT_CONTRACT_OK":
        raise SystemExit(f"unexpected contract output: {output!r}")
    print(output)
