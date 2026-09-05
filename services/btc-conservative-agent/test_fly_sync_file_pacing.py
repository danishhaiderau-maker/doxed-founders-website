import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "fly-sync-file-pacing.ps1"
PWSH = shutil.which("pwsh")


def _run(body, input_payload=None):
    if not PWSH:
        pytest.skip("PowerShell runtime unavailable; executable pacing QA not passed")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command",
         f"$ErrorActionPreference='Stop'; . '{str(HELPER).replace(chr(39), chr(39)*2)}'; {body}"],
        capture_output=True, text=True, timeout=20, input=input_payload,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_small_receipts_have_bounded_faster_pacing_and_no_burst():
    results = _run("@(0,50,200,450,600,999) | ForEach-Object { "
                   "$delay=Get-FlySyncInterFileDelayMs -FileBytes 732 -RequestElapsedMs $_ -AdaptiveThrottleMs 1000; "
                   "[pscustomobject]@{elapsed=$_;delay=$delay} } | ConvertTo-Json -Compress")
    for result in results:
        assert 50 <= result["delay"] < 1500
        assert result["elapsed"] + result["delay"] >= 500


def test_large_slow_or_pressure_requests_keep_protective_delay():
    results = _run("@("
                   "(Get-FlySyncInterFileDelayMs -FileBytes 16385 -RequestElapsedMs 100 -AdaptiveThrottleMs 1000),"
                   "(Get-FlySyncInterFileDelayMs -FileBytes 732 -RequestElapsedMs 1000 -AdaptiveThrottleMs 1000),"
                   "(Get-FlySyncInterFileDelayMs -FileBytes 732 -RequestElapsedMs 100 -AdaptiveThrottleMs 1100),"
                   "(Get-FlySyncInterFileDelayMs -FileBytes 732 -RequestElapsedMs 100 -AdaptiveThrottleMs 5000)"
                   ") | ConvertTo-Json -Compress")
    assert results == [1500, 1500, 1500, 5000]


def test_invalid_observations_fail_closed():
    results = _run("$cases=@(@{FileBytes=-1;RequestElapsedMs=0;AdaptiveThrottleMs=1000},"
                   "@{FileBytes=1;RequestElapsedMs=[double]::NaN;AdaptiveThrottleMs=1000},"
                   "@{FileBytes=1;RequestElapsedMs=[double]::PositiveInfinity;AdaptiveThrottleMs=1000},"
                   "@{FileBytes=1;RequestElapsedMs=-1;AdaptiveThrottleMs=1000},"
                   "@{FileBytes=1;RequestElapsedMs=1;AdaptiveThrottleMs=0}); "
                   "@($cases | ForEach-Object { try { Get-FlySyncInterFileDelayMs @_ ; 'UNEXPECTED_PASS' } "
                   "catch { $_.Exception.Message } }) | ConvertTo-Json -Compress")
    assert results == ["INVALID_SYNC_PACING_OBSERVATION"] * 5


def test_helper_cannot_mutate_or_start_a_transfer():
    source = HELPER.read_text(encoding="utf-8")
    for forbidden in ("Start-Sleep", "Invoke-WebRequest", "HttpClient", "Remove-Item",
                      "Set-Content", "Start-ScheduledTask", "Stop-Process"):
        assert forbidden not in source


def test_review_patch_applies_cleanly_and_candidate_parses_without_execution():
    patch = ROOT / "diagnostics" / "sync-small-file-pacing-review.patch"
    checked = subprocess.run(["git", "apply", "--check", str(patch)], cwd=ROOT,
                             capture_output=True, text=True, timeout=20)
    assert checked.returncode == 0, checked.stderr
    source = (ROOT / "scripts" / "sync-fly-bot-data.ps1").read_text(encoding="utf-8-sig")
    old_import = '. (Join-Path $scriptDir "fly-sync-backoff.ps1")'
    old_delay = '$fileThrottleMs = [Math]::Max($baseInterFileThrottleMs, $adaptiveThrottleMs)'
    assert source.count(old_import) == source.count(old_delay) == 1
    candidate = source.replace(old_import, old_import + '\n. (Join-Path $scriptDir "fly-sync-file-pacing.ps1")')
    candidate = candidate.replace(old_delay,
        '$fileThrottleMs = Get-FlySyncInterFileDelayMs `\n'
        '      -FileBytes $remoteSize `\n'
        '      -RequestElapsedMs $chunkRequestElapsedMs `\n'
        '      -AdaptiveThrottleMs $adaptiveThrottleMs `\n'
        '      -BaseInterFileThrottleMs $baseInterFileThrottleMs `\n'
        '      -BaseInterChunkThrottleMs $baseInterChunkThrottleMs')
    result = _run("$source=[Console]::In.ReadToEnd(); "
                  "$parseTokens=$null; $parseErrors=$null; "
                  "[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$parseTokens,[ref]$parseErrors) | Out-Null; "
                  "ConvertTo-Json -InputObject @($parseErrors | ForEach-Object Message) -Compress", candidate)
    assert result == []
