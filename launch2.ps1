$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $repoRoot
$Host.UI.RawUI.WindowTitle='DCF-Window2-dev-tunnel'
cmd /c dev-tunnel.cmd
