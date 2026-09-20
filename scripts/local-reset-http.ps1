# Shared authenticated loopback route for Laptop Fresh Collection. Production
# and the disposable HttpListener acceptance test call this exact handler.

function Get-LocalResetAllowedOrigins {
  return @(
    'https://doxxedcrypto.digital',
    'https://www.doxxedcrypto.digital',
    'https://bot.doxxedcrypto.digital',
    'https://doxed-btc-bot.fly.dev',
    'http://localhost:3000',
    'http://127.0.0.1:3000'
  )
}

function Write-LocalResetCors {
  param(
    [System.Net.HttpListenerRequest]$Request,
    [System.Net.HttpListenerResponse]$Response
  )
  $origin = [string]$Request.Headers['Origin']
  if ($origin -and (Get-LocalResetAllowedOrigins) -contains $origin) {
    $Response.Headers.Add('Access-Control-Allow-Origin', $origin)
  }
  $Response.Headers.Add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
  $Response.Headers.Add(
    'Access-Control-Allow-Headers',
    'Content-Type, X-Local-Reset-Capability'
  )
  $Response.Headers.Add('Access-Control-Allow-Private-Network', 'true')
}

function Send-LocalResetJson {
  param(
    [System.Net.HttpListenerResponse]$Response,
    [object]$Payload,
    [int]$Status = 200
  )
  $json = $Payload | ConvertTo-Json -Compress -Depth 12
  $bytes = [Text.Encoding]::UTF8.GetBytes($json)
  $Response.ContentType = 'application/json; charset=utf-8'
  $Response.StatusCode = $Status
  $Response.OutputStream.Write($bytes, 0, $bytes.Length)
  $Response.Close()
}

function Test-LocalResetOrigin([System.Net.HttpListenerRequest]$Request) {
  $origin = [string]$Request.Headers['Origin']
  return $origin -in @(Get-LocalResetAllowedOrigins)
}

function Test-LocalResetCapability {
  param(
    [System.Net.HttpListenerRequest]$Request,
    [Parameter(Mandatory = $true)][string]$CapabilityHashPath
  )
  if (-not (Test-Path -LiteralPath $CapabilityHashPath -PathType Leaf)) { return $false }
  $hashItem = Get-Item -LiteralPath $CapabilityHashPath -Force -ErrorAction Stop
  if (($hashItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
      $hashItem.Length -gt 80) { return $false }
  $expected = ([string](Get-Content -LiteralPath $CapabilityHashPath -Raw -ErrorAction Stop)).Trim()
  $provided = [string]$Request.Headers['X-Local-Reset-Capability']
  if ($expected -notmatch '^[0-9a-f]{64}$' -or $provided.Length -lt 32) { return $false }
  $bytes = [Text.Encoding]::UTF8.GetBytes($provided)
  $sha = [Security.Cryptography.SHA256]::Create()
  try { $actual = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
  finally { $sha.Dispose(); [Array]::Clear($bytes, 0, $bytes.Length) }
  $difference = 0
  for ($index = 0; $index -lt 64; $index++) {
    $difference = $difference -bor (
      [int][char]$actual[$index] -bxor [int][char]$expected[$index]
    )
  }
  return $difference -eq 0
}

function Invoke-LocalResetCli {
  param(
    [Parameter(Mandatory = $true)][string]$CliPath,
    [string[]]$Arguments,
    [string]$StdinJson = ''
  )
  if (-not (Test-Path -LiteralPath $CliPath -PathType Leaf)) {
    throw [InvalidOperationException]::new('LOCAL_RESET_CLI_MISSING')
  }
  $output = if ($StdinJson) {
    $StdinJson | & python $CliPath @Arguments
  } else {
    & python $CliPath @Arguments
  }
  $exitCode = $LASTEXITCODE
  $parsed = ($output -join "`n") | ConvertFrom-Json -ErrorAction Stop
  if ($exitCode -ne 0) {
    throw [InvalidOperationException]::new([string]$parsed.error)
  }
  return $parsed
}

function Read-LocalResetBoundedRequestJson(
  [System.Net.HttpListenerRequest]$Request
) {
  if ($Request.ContentLength64 -lt 1 -or $Request.ContentLength64 -gt 8192) {
    throw [InvalidOperationException]::new('LOCAL_RESET_REQUEST_SIZE_INVALID')
  }
  $reader = [IO.StreamReader]::new(
    $Request.InputStream, [Text.Encoding]::UTF8, $true, 1024, $true
  )
  try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}

function Invoke-LocalResetApiRoute {
  param(
    [System.Net.HttpListenerRequest]$Request,
    [System.Net.HttpListenerResponse]$Response,
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$CliPath,
    [Parameter(Mandatory = $true)][string]$CapabilityHashPath,
    [Parameter(Mandatory = $true)][scriptblock]$StartWorker,
    [switch]$EnableLocalReset
  )
  Write-LocalResetCors -Request $Request -Response $Response
  try {
    if (-not (Test-LocalResetOrigin $Request)) {
      Send-LocalResetJson -Response $Response -Payload @{
        ok=$false; error='LOCAL_RESET_ORIGIN_REFUSED'
      } -Status 403
      return
    }
    if (-not (Test-Path -LiteralPath $CapabilityHashPath -PathType Leaf)) {
      Send-LocalResetJson -Response $Response -Payload @{
        ok=$false; error='LOCAL_RESET_CAPABILITY_NOT_PROVISIONED';
        message='Laptop reset unavailable; connect local controller'
      } -Status 503
      return
    }
    if (-not (Test-LocalResetCapability -Request $Request -CapabilityHashPath $CapabilityHashPath)) {
      Send-LocalResetJson -Response $Response -Payload @{
        ok=$false; error='LOCAL_RESET_UNAUTHORIZED'
      } -Status 401
      return
    }
    if ($Path -eq '/api/local-research-reset/v1/capability') {
      if ($Request.HttpMethod -ne 'GET') {
        Send-LocalResetJson -Response $Response -Payload @{
          ok=$false; error='METHOD_NOT_ALLOWED'
        } -Status 405
        return
      }
      $payload = Invoke-LocalResetCli -CliPath $CliPath -Arguments @('capability')
      $payload | Add-Member -NotePropertyName reset_enabled -NotePropertyValue ([bool]$EnableLocalReset) -Force
      Send-LocalResetJson -Response $Response -Payload $payload
      return
    }
    if ($Path -eq '/api/local-research-reset/v1/requests') {
      if ($Request.HttpMethod -ne 'POST') {
        Send-LocalResetJson -Response $Response -Payload @{
          ok=$false; error='METHOD_NOT_ALLOWED'
        } -Status 405
        return
      }
      # Authentication is not rollout acceptance. Production stays disabled
      # until installed owner/relaunch, canonical-root and viewer gates pass.
      if (-not $EnableLocalReset) {
        Send-LocalResetJson -Response $Response -Payload @{
          ok=$false; error='LOCAL_RESET_RELEASE_NOT_ENABLED';
          message='Laptop reset unavailable - local controller readiness required'
        } -Status 503
        return
      }
      $body = Read-LocalResetBoundedRequestJson $Request
      $queued = Invoke-LocalResetCli -CliPath $CliPath -Arguments @('queue') -StdinJson $body
      $operationId = [string]$queued.operation_id
      if ($operationId -notmatch '^[0-9a-f]{32}$') {
        throw [InvalidOperationException]::new('LOCAL_RESET_OPERATION_ID_INVALID')
      }
      if ([string]$queued.status -ne 'COMPLETE') { & $StartWorker $operationId }
      Send-LocalResetJson -Response $Response -Payload @{
        operation_id=$operationId
        request_id=[string]$queued.request_id
        status=[string]$queued.status
        fly_mutation_requested=$false
        replay=[bool]$queued.replay
        status_url="/api/local-research-reset/v1/operations/$operationId"
      } -Status 202
      return
    }
    if ($Path -match '^/api/local-research-reset/v1/operations/(?<id>[0-9a-f]{32})$') {
      if ($Request.HttpMethod -ne 'GET') {
        Send-LocalResetJson -Response $Response -Payload @{
          ok=$false; error='METHOD_NOT_ALLOWED'
        } -Status 405
        return
      }
      $payload = Invoke-LocalResetCli -CliPath $CliPath -Arguments @(
        'status', '--operation-id', $matches['id']
      )
      Send-LocalResetJson -Response $Response -Payload $payload
      return
    }
    Send-LocalResetJson -Response $Response -Payload @{
      ok=$false; error='LOCAL_RESET_PATH_NOT_FOUND'
    } -Status 404
  } catch [InvalidOperationException] {
    $message = $_.Exception.Message
    $status = if ($message -match 'REPLAY_CONFLICT|GENERATION_CHANGED|BLOCKED_PENDING') {
      409
    } elseif ($message -eq 'LOCAL_RESET_OPERATION_NOT_FOUND') {
      404
    } else { 400 }
    Send-LocalResetJson -Response $Response -Payload @{ ok=$false; error=$message } -Status $status
  } catch {
    # Do not return exception text: it may contain a path or transport detail.
    if (-not $Response.OutputStream.CanWrite) { return }
    Send-LocalResetJson -Response $Response -Payload @{
      ok=$false; error='LOCAL_RESET_CONTROLLER_FAILED'
    } -Status 500
  }
}
