param([switch]$Once, [switch]$RepairMissingLocal, [int]$IntervalSec = 300, [string]$VaultEnv = "")

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$vaultCandidates = @(
  $VaultEnv,
  $env:DOXXED_HOME_BOT_VAULT,
  (Join-Path $repoRoot "doxedcryptofounder-secrets\vault\home-bot.env"),
  (Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
$vaultEnv = $vaultCandidates | Select-Object -First 1
if (-not $vaultEnv) { throw "home-bot.env was not found; pass -VaultEnv explicitly." }
. (Join-Path $scriptDir "home-bot-vault-env.ps1")
Import-HomeBotVaultConfig -VaultEnvPath $vaultEnv
if (-not $env:BOT_ADMIN_TOKEN) { throw "BOT_ADMIN_TOKEN is required." }

$argsList = @((Join-Path $scriptDir "research-stability-supervisor.py"), "--repo", $repoRoot, "--interval-seconds", "$IntervalSec")
if (-not $Once) { $argsList += "--loop" }
if ($RepairMissingLocal) { $argsList += "--repair-missing-local" }

# The Python owner uses an exclusive status write and reports duplicate owners;
# this launcher never starts or restarts trading and never exposes the token.
python @argsList
exit $LASTEXITCODE
