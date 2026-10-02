"""Network-free contract for bounded sync error diagnostics.

The Fly client may inspect a remote error briefly to classify it, but raw
response text can contain sensitive upstream details.  This test proves the
projection is codes plus a digest only and that the request/chunk paths use
the projection rather than interpolating exception text into output.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile


SCRIPTS = Path(__file__).resolve().parent


def _ps_literal(value: str) -> str:
    return value.replace("'", "''")


def _helper_source() -> str:
    source = (SCRIPTS / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    start = source.index("function Test-DataSyncResourcePressureError")
    end = source.index("function Get-DataSyncRetryDelaySec", start)
    return source[start:end]


def _request_function_source() -> str:
    source = (SCRIPTS / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    start = source.index("function Test-DataSyncResourcePressureError")
    end = source.index("function New-DataSyncManifestUri", start)
    return source[start:end]


def _run_helper_contract() -> str:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("PowerShell is required for sync error-redaction checks")

    harness = f"""
$ErrorActionPreference='Stop'
function Test-FlySyncResourcePressureMessage {{ param([string]$Message = '') return $false }}
$functionSource=@'
{_helper_source()}
'@
Invoke-Expression $functionSource
$secret='do-not-print-this-secret'
$summary=Get-DataSyncSafeErrorSummary -Message "Fly sync HTTP 409 identity mismatch $secret" -ErrorDetails "body=$secret"
$json=$summary | ConvertTo-Json -Compress
if($summary.classification -cne 'HTTP_409' -or
   $summary.http_status -ne 409 -or
   $summary.server_class -cne 'IDENTITY_MISMATCH' -or
   $summary.response_sha256 -cnotmatch '^[0-9a-f]{{64}}$' -or
   $summary.response_bytes -lt $secret.Length -or
   $json.Contains($secret) -or
   $json.Contains('identity mismatch')) {{
  throw 'SYNC_ERROR_REDACTION_DYNAMIC_CONTRACT_FAILED'
}}
$normalized=Get-DataSyncSafeErrorSummary -Message 'Fly sync HTTP 409 server_class=PAGE_HASH_MISMATCH' -ErrorDetails ''
if($normalized.server_class -cne 'PAGE_HASH_MISMATCH') {{
  throw 'NORMALIZED_SERVER_CLASS_WAS_LOST'
}}
Write-Output 'SYNC_ERROR_REDACTION_CONTRACT_OK'
"""
    with tempfile.TemporaryDirectory(prefix="btc-sync-redaction-contract-") as temp:
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


def _run_request_catch_contract() -> str:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("PowerShell is required for sync error-redaction checks")

    harness = f"""
$ErrorActionPreference='Stop'
$WarningPreference='SilentlyContinue'
$manifestTimeoutSec=2
$transportAttempts=1
$resourcePressureCircuitThreshold=2
$headers=@{{}}
$script:secret='do-not-print-this-secret'
function Test-FlySyncResourcePressureMessage {{ param([string]$Message = '') return $false }}
function Invoke-RestMethod {{
  param([string]$Uri,[string]$Method,[object]$Headers,[int]$TimeoutSec,[string]$ContentType,[string]$Body)
  throw "Fly sync HTTP 409 identity mismatch $script:secret"
}}
$functionSource=@'
{_request_function_source()}
'@
Invoke-Expression $functionSource
$warnings=@()
$message=''
try {{
  Invoke-DataSyncJsonRequest -Stage 'acknowledgement_finalize' -Uri 'https://example.invalid' `
    -Method 'Post' -MaxAttempts 1 -MaxElapsedSec 10 -WarningVariable warnings | Out-Null
}} catch {{ $message=[string]$_.Exception.Message }}
$observed="$message`n$($warnings -join "`n")"
if(-not $message.Contains('failure=HTTP_409') -or
   -not $message.Contains('server_class=IDENTITY_MISMATCH') -or
   -not $observed.Contains('error_class=HTTP_409') -or
   $observed.Contains($script:secret) -or
   $observed.Contains('identity mismatch')) {{
  throw 'SYNC_REQUEST_CATCH_REDACTION_FAILED'
}}
Write-Output 'SYNC_REQUEST_CATCH_REDACTION_OK'
"""
    with tempfile.TemporaryDirectory(prefix="btc-sync-request-redaction-") as temp:
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


def _assert_request_and_chunk_paths_are_safe() -> None:
    source = (SCRIPTS / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    request_start = source.index("function Invoke-DataSyncJsonRequest")
    request_end = source.index("function New-DataSyncManifestUri", request_start)
    request_body = source[request_start:request_end]
    chunk_marker = source.index("$generationChanged = (", request_end)
    chunk_start = source.rfind("        } catch {", request_end, chunk_marker)
    chunk_end = source.index("Start-Sleep -Seconds (Get-DataSyncRetryDelaySec", chunk_start)
    chunk_body = source[chunk_start:chunk_end]

    assert "function Get-DataSyncSafeErrorSummary" in source
    assert "error=$($_.Exception.Message)" not in source
    assert "Get-DataSyncSafeErrorSummary -Message $rawMessage -ErrorDetails $rawErrorDetails" in request_body
    assert "error_class=$($safeFailure.classification)" in request_body
    assert "response_sha256=$($safeFailure.response_sha256)" in request_body
    assert "Get-DataSyncSafeErrorSummary -Message $rawMessage -ErrorDetails $rawErrorDetails" in chunk_body
    assert "failure=$($safeFailure.classification)" in chunk_body
    assert "response_sha256=$($safeFailure.response_sha256)" in chunk_body


if __name__ == "__main__":
    _assert_request_and_chunk_paths_are_safe()
    output = _run_helper_contract()
    if output != "SYNC_ERROR_REDACTION_CONTRACT_OK":
        raise SystemExit(f"unexpected contract output: {output!r}")
    request_output = _run_request_catch_contract()
    if request_output != "SYNC_REQUEST_CATCH_REDACTION_OK":
        raise SystemExit(f"unexpected request contract output: {request_output!r}")
    print(output)
