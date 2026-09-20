from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCERS = (
    ROOT / "scripts" / "sync-fly-bot-data.ps1",
)


def function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    next_function = source.find("\nfunction ", start + 1)
    return source[start:] if next_function < 0 else source[start:next_function]


sources = [path.read_text(encoding="utf-8-sig") for path in PRODUCERS]
helpers = [function_body(source, "Resolve-DataSyncManifestMemberPath") for source in sources]
assert len(helpers) == 1

for source in sources:
    helper = function_body(source, "Resolve-DataSyncManifestMemberPath")
    for required in (
        "$MemberPath.Contains('\\')",
        "$MemberPath.Contains(':')",
        "$MemberPath.Split('/')",
        "[Text.Encoding]::UTF8.GetByteCount($MemberPath) -gt 1024",
        "[IO.Path]::IsPathRooted($MemberPath)",
        "$rootFull + [IO.Path]::DirectorySeparatorChar",
        "StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)",
        "Assert-FlyBundleUnlinkedPath -Path $rootFull",
        "Assert-FlyBundleUnlinkedPath -Path $candidate",
        "DATA_SYNC_MANIFEST_MEMBER_INVALID",
        "DATA_SYNC_MANIFEST_MEMBER_OUTSIDE_ROOT",
        "\\x7F",
    ):
        assert required in helper
    admission = source.index("$admittedMemberPaths =")
    assert admission < source.index("$localRetirementEnabled")
    assert admission < source.index("$incomingGrowth =")
    assert admission < source.index("Receive-FlyTransportBundles")
    assert "$candidate = $admittedMemberPaths[[string]$row.path]" in source
    assert "$local = Resolve-DataSyncManifestMemberPath -Root $targetRoot -MemberPath $rel" in source
    assert "$localAfterParentCreate = Resolve-DataSyncManifestMemberPath" in source


def run_direct_contract(producer: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return
    producer_source = producer.read_text(encoding="utf-8-sig")
    bundle_source = (producer.parent / "fly-sync-bundles.ps1").read_text(encoding="utf-8-sig")
    imported_functions = "\n".join(
        (
            function_body(bundle_source, "Assert-FlyBundleUnlinkedPath"),
            function_body(producer_source, "Resolve-DataSyncManifestMemberPath"),
        )
    )
    with tempfile.TemporaryDirectory(prefix="fly-member-admission-") as temporary:
        root_literal = str(Path(temporary) / "mirror").replace("'", "''")
        command = rf"""
$ErrorActionPreference='Stop'
{imported_functions}
$root='{root_literal}'
[void][IO.Directory]::CreateDirectory($root)
$valid=Resolve-DataSyncManifestMemberPath -Root $root -MemberPath 'nested/good.jsonl'
$prefix=[IO.Path]::GetFullPath($root).TrimEnd('\','/')+[IO.Path]::DirectorySeparatorChar
if(-not $valid.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){{throw 'VALID_MEMBER_OUTSIDE'}}
$invalid=@(
  '..\outside.txt',
  '../mirror-sibling/evil.txt',
  'nested\evil.txt',
  '/absolute.txt',
  'C:/drive.txt',
  '//server/share.txt',
  'nested//empty.txt',
  'nested/../escape.txt',
  'nested/COM1.txt',
  ('nested/'+('é'*510)+'.txt'),
  "nested/$([char]0x7f)evil.txt"
)
foreach($member in $invalid){{
  try{{Resolve-DataSyncManifestMemberPath -Root $root -MemberPath $member | Out-Null; throw 'INVALID_MEMBER_ACCEPTED'}}
  catch{{
    if($_.Exception.Message -cnotin @('DATA_SYNC_MANIFEST_MEMBER_INVALID','DATA_SYNC_MANIFEST_MEMBER_OUTSIDE_ROOT')){{throw}}
  }}
}}
Write-Output 'DIRECT_MEMBER_CONTRACT_OK'
"""
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "DIRECT_MEMBER_CONTRACT_OK" in completed.stdout


for producer in PRODUCERS:
    run_direct_contract(producer)

for source in sources:
    publish = "Publish-MirrorCandidate -Candidate $candidate -Destination $localBeforePublish"
    assert publish in source
    publish_at = source.index(publish)
    revalidate = "Resolve-DataSyncManifestMemberPath -Root $targetRoot -MemberPath $rel"
    assert source.rfind(revalidate, 0, publish_at) > publish_at - 500

print("Fly sync manifest member pre-admission checks passed")
