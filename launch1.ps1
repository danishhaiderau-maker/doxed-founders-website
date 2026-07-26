$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $repoRoot
$Host.UI.RawUI.WindowTitle='DCF-Window1-dev-lan'
cmd /c dev-lan.cmd
