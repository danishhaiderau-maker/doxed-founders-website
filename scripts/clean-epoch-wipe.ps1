# clean-epoch-wipe: DRY-RUN plan by default; execute needs --epoch, the certification confirm token and the plan sha.
#   scripts\clean-epoch-wipe.ps1 plan --scope laptop --epoch ce-YYYYMMDD-label --out C:\DoxxedCrypto\clean-epoch\plan-laptop.json
#   scripts\clean-epoch-wipe.ps1 execute --scope laptop --epoch ce-... --confirm DELETE-PRE-EPOCH:ce-...:<cert8> --expect-plan-sha256 <sha>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if ($args.Count -eq 0) { $args = @("plan", "--scope", "laptop") }
& python (Join-Path $repo "services\btc-conservative-agent\clean_epoch_wipe.py") @args
exit $LASTEXITCODE
