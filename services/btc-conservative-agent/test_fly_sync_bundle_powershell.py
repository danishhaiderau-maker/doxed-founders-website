import base64
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
PWSH = Path("C:/Users/danis/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe")


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell runtime unavailable")
@pytest.mark.parametrize("defect", [None, "hash", "waiting", "interleaved", "foreign-wait", "late-wait", "reuse", "reuse-escape", "reuse-flag", "reuse-size", "failed", "failed-extra", "failed-malformed"])
def test_parent_promotes_verified_staging_through_original_checkpoint(tmp_path, defect):
    payload = '{"sample":1}'
    relative = "v3/market_segments/11/" + "1" * 64 + ".json"
    manifest = {"inventory_generation_id": "a" * 64, "inventory_sha256": "a" * 64,
                "source_git_rev": "source", "collection_epoch_id": "epoch",
                "tile_registry_signature": "tile", "schema": "fly_runtime_incremental_sync_v1",
                "inventory_status": "CURRENT", "inventory_authoritative": True,
                "inventory_ack_eligible": True,
                "fixture_defect": defect,
                "files": [{"path": relative, "size": len(payload), "inode": 123,
                           "mtime_ns": 456, "consistency_mode": "strict_generation_v1",
                           "fixture_payload": payload}]}
    if defect == "reuse-size":
        manifest["files"][0]["size"] += 1
    encoded = base64.b64encode(json.dumps(manifest).encode()).decode()
    target = tmp_path / "mirror"
    if defect in {"reuse", "reuse-size"}:
        (target / relative).parent.mkdir(parents=True)
        (target / relative).write_text(payload)
    no_publish = "function Publish-MirrorCandidate { throw 'REUSE_MUST_NOT_PUBLISH' }" if defect in {"reuse", "reuse-size"} else ""
    script = f"""
$ErrorActionPreference='Stop'
. '{ROOT.as_posix()}/scripts/fly-mirror-atomic.ps1'
. '{ROOT.as_posix()}/scripts/fly-sync-bundles.ps1'
{no_publish}
$manifest=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))|ConvertFrom-Json
$state=@{{}}
$script:saves=0
$script:phases=@()
$script:details=@()
try {{
 $result=Receive-FlyTransportBundles -Manifest $manifest -SourceUrl 'https://doxed-btc-bot.fly.dev' -AdminToken 'offline-fixture' -TargetRoot '{target.as_posix()}' -ClientScript '{ROOT.as_posix()}/scripts/test-support/bundle-staging-fixture.py' -SyncState $state -SaveCheckpoint {{$script:saves+=1}} -Progress {{param($n,$phase,$detail) $script:phases+=@{{files=$n;phase=$phase}}; $script:details+=@{{detail=$detail;durableSaves=$script:saves}}}}
 @{{result=$result;state=$state;saves=$script:saves;phases=$script:phases;details=$script:details}}|ConvertTo-Json -Depth 10 -Compress
}} catch {{ Write-Output $_.Exception.Message; @{{phases=$script:phases;details=$script:details}}|ConvertTo-Json -Depth 10 -Compress; exit 7 }}
"""
    completed = subprocess.run([str(PWSH), "-NoProfile", "-Command", script],
                               capture_output=True, text=True, timeout=30)
    if str(defect).startswith('failed'):
        assert completed.returncode == 7, completed.stdout + completed.stderr
        result=json.loads(completed.stdout.splitlines()[-1])
        assert result['phases'][-1] == {'files':1,'phase':'bundle_failed'}
        detail=result['details'][-1]['detail']
        assert detail['Failed'] is True and detail['VerifiedBytes'] == len(payload)
        assert detail['ReusedBytes'] == 0
        assert 'PRIVATE_SENTINEL' not in completed.stdout
        assert ('failure_context=' in completed.stdout) is (defect == 'failed')
        assert (target / relative).read_text() == payload
    elif defect in {"hash", "foreign-wait", "late-wait", "reuse-escape", "reuse-flag", "reuse-size"}:
        code = {"hash": "BUNDLE_STAGE_HASH_MISMATCH", "foreign-wait": "BUNDLE_RECEIPT_IDENTITY",
                "late-wait": "BUNDLE_INDEX_WAIT_INVALID", "reuse-escape": "BUNDLE_REUSE_PATH_MISMATCH",
                "reuse-flag": "BUNDLE_REUSE_FLAG_INVALID", "reuse-size": "BUNDLE_STAGE_SIZE_MISMATCH"}[defect]
        assert completed.returncode == 7 and code in completed.stdout
        if defect not in {"reuse-flag", "reuse-size"}:
            assert not (target / relative).exists()
    else:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        result = json.loads(completed.stdout)
        assert result["result"]["Files"] == 1 and result["result"]["AckSent"] is False
        assert result["saves"] == 1
        assert result["details"][-1]["durableSaves"] == 1
        assert result["details"][-1]["detail"]["VerifiedBytes"] == len(payload)
        assert result["details"][-1]["detail"]["ReusedBytes"] == (len(payload) if defect == "reuse" else 0)
        assert not Path(result["result"]["StagingRoot"]).exists()
        assert result["state"][relative]["size"] == len(payload)
        assert result["state"][relative]["mtime_ns"] == 456
        assert (target / relative).read_text() == payload
        if defect == "waiting":
            assert result["phases"][0] == {"files": 0, "phase": "bundle_index_wait"}
            assert result["phases"][1] == {"files": 1, "phase": "bundle_verified"}
        if defect == "interleaved":
            assert result["phases"][:2] == [{"files":1,"phase":"bundle_verified"},{"files":1,"phase":"bundle_index_wait"}]
