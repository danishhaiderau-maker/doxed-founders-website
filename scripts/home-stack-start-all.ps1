# Deprecated entry — delegates to visible-console orchestrator (detached starts fail on this machine).
param(
  [int]$BotPort = 0,
  [int]$AnalyzerPort = 0
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$args = @()
if ($BotPort -gt 0) { $args += "-BotPort", "$BotPort" }
if ($AnalyzerPort -gt 0) { $args += "-AnalyzerPort", "$AnalyzerPort" }
& (Join-Path $scriptDir "home-stack-start-everything.ps1") @args
