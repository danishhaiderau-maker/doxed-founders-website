param(
  [Parameter(Mandatory = $true)][int]$Port,
  [Parameter(Mandatory = $true)][string]$RouteModule,
  [Parameter(Mandatory = $true)][string]$FixtureCli,
  [Parameter(Mandatory = $true)][string]$CapabilityHashPath,
  [Parameter(Mandatory = $true)][string]$ReadyPath,
  [switch]$DisableReset
)

$ErrorActionPreference = 'Stop'
. $RouteModule

$listener = [Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
try {
  $listener.Start()
  [IO.File]::WriteAllText($ReadyPath, 'READY', [Text.UTF8Encoding]::new($false))
  while ($listener.IsListening) {
    $context = $listener.GetContext()
    $request = $context.Request
    $response = $context.Response
    $path = $request.Url.AbsolutePath.TrimEnd('/')
    if (-not $path) { $path = '/' }
    try {
      Invoke-LocalResetApiRoute -Request $request -Response $response `
        -Path $path -CliPath $FixtureCli -CapabilityHashPath $CapabilityHashPath `
        -EnableLocalReset:(-not $DisableReset) `
        -StartWorker {
          param($OperationId)
          $workerArgs = @($FixtureCli, 'run', '--operation-id', $OperationId)
          if ($IsWindows) {
            Start-Process -FilePath 'python' -ArgumentList $workerArgs -WindowStyle Hidden | Out-Null
          } else {
            # WindowStyle is a Windows-only process option; keep the Linux
            # acceptance fixture cross-platform without changing the route.
            Start-Process -FilePath 'python' -ArgumentList $workerArgs | Out-Null
          }
        }
    } catch {
      try { $response.Abort() } catch { }
    }
  }
} finally {
  if ($listener.IsListening) { $listener.Stop() }
  $listener.Close()
}
