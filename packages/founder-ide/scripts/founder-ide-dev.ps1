[CmdletBinding()]
param(
    [ValidateSet("Status", "Extension", "Workbench")]
    [string]$Mode = "Status",

    [string]$CheckoutPath = "",
    [string]$CacheRoot = "",
    [string]$NodePath = "",
    [string]$InstalledExe = "",

    [switch]$Force,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Find-MonorepoRoot {
    $candidate = Split-Path -Parent $PSScriptRoot
    for ($i = 0; $i -lt 8 -and $candidate; $i++) {
        if (Test-Path (Join-Path $candidate "packages\founder-ide\upstream\overlay\MANIFEST.json")) {
            return (Resolve-Path $candidate).Path
        }
        $candidate = Split-Path -Parent $candidate
    }
    throw "Could not locate the Founder OS monorepo root."
}

function Get-CompositeHash {
    param(
        [Parameter(Mandatory = $true)][string[]]$Paths,
        [Parameter(Mandatory = $true)][string]$BasePath
    )

    $lines = foreach ($path in ($Paths | Sort-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Hash input is missing: $path"
        }
        $resolved = (Resolve-Path -LiteralPath $path).Path
        $baseUri = [Uri]::new($BasePath.TrimEnd("\") + "\")
        $fileUri = [Uri]::new($resolved)
        $relative = [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($fileUri).ToString())
        $digest = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
        "$relative`t$digest"
    }

    $payload = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($payload))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Resolve-NodeExecutable {
    if ($NodePath) {
        if (-not (Test-Path -LiteralPath $NodePath -PathType Leaf)) {
            throw "Node executable not found: $NodePath"
        }
        return (Resolve-Path -LiteralPath $NodePath).Path
    }

    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Node.js was not found. Pass -NodePath with the pinned Node executable."
    }
    return $command.Source
}

function Format-NamedArgument {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )
    return "$Name=`"$Value`""
}

function Assert-PinnedCheckout {
    param(
        [Parameter(Mandatory = $true)][string]$Checkout,
        [Parameter(Mandatory = $true)][string]$ExpectedCommit,
        [Parameter(Mandatory = $true)][string]$ExpectedNode,
        [Parameter(Mandatory = $true)][string]$NodeExecutable
    )

    if (-not (Test-Path (Join-Path $Checkout "product.json"))) {
        throw "Founder IDE checkout is missing product.json: $Checkout"
    }
    if (-not (Test-Path (Join-Path $Checkout ".git"))) {
        throw "Warm workbench mode requires a git checkout so its upstream identity can be proven."
    }

    $actualCommit = (& git -c "safe.directory=$Checkout" -C $Checkout rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $actualCommit) {
        throw "Could not read the Founder IDE upstream commit at $Checkout"
    }
    if ($actualCommit -ne $ExpectedCommit) {
        throw "Founder IDE upstream drift: expected $ExpectedCommit, found $actualCommit"
    }

    $trackedStatus = @(& git -c "safe.directory=$Checkout" -C $Checkout status --porcelain=v1 --untracked-files=no)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the Founder IDE checkout state at $Checkout"
    }
    $unsafeChanges = @(
        $trackedStatus | Where-Object {
            $_.Length -ge 2 -and ($_[0] -ne " " -or $_[1] -eq "D")
        }
    )
    if ($unsafeChanges.Count -gt 0) {
        $sample = ($unsafeChanges | Select-Object -First 5) -join "; "
        throw "Warm checkout has staged or deleted tracked files and cannot be trusted: $sample"
    }

    $actualNode = (& $NodeExecutable --version).Trim().TrimStart("v")
    if ($LASTEXITCODE -ne 0 -or $actualNode -ne $ExpectedNode) {
        throw "Workbench mode requires Node $ExpectedNode; found $actualNode at $NodeExecutable"
    }
}

function Copy-ExtensionStage {
    param(
        [Parameter(Mandatory = $true)][string]$ExtensionRoot,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$StageRoot,
        [Parameter(Mandatory = $true)][string]$NodeExecutable,
        [Parameter(Mandatory = $true)][string]$ExtensionHash
    )

    $readyFile = Join-Path $StageRoot ".founder-extension-ready.json"
    if ((-not $Force) -and (Test-Path $readyFile)) {
        return $readyFile
    }

    New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $StageRoot "out") | Out-Null

    foreach ($file in @("package.json", "README.md", "LICENSE")) {
        Copy-Item -LiteralPath (Join-Path $ExtensionRoot $file) -Destination (Join-Path $StageRoot $file) -Force
    }
    Copy-Item -LiteralPath (Join-Path $ExtensionRoot "resources") -Destination $StageRoot -Recurse -Force

    $tscCandidates = @(
        (Join-Path $ExtensionRoot "node_modules\typescript\bin\tsc"),
        (Join-Path $RepoRoot "node_modules\typescript\bin\tsc")
    )
    $tsc = $tscCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $tsc) {
        throw "TypeScript compiler not found in the extension or monorepo dependencies."
    }

    $started = Get-Date
    & $NodeExecutable $tsc -p (Join-Path $ExtensionRoot "tsconfig.json") --outDir (Join-Path $StageRoot "out")
    if ($LASTEXITCODE -ne 0) {
        throw "Founder extension compilation failed with exit code $LASTEXITCODE"
    }

    [ordered]@{
        extensionHash = $ExtensionHash
        compiledAt = (Get-Date).ToUniversalTime().ToString("o")
        elapsedSeconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)
        source = $ExtensionRoot
    } | ConvertTo-Json | Set-Content -LiteralPath $readyFile -Encoding UTF8

    return $readyFile
}

$repoRoot = Find-MonorepoRoot
$manifestPath = Join-Path $repoRoot "packages\founder-ide\upstream\overlay\MANIFEST.json"
$overlayRoot = Split-Path -Parent $manifestPath
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$extensionRoot = Join-Path $repoRoot "packages\founder-ide-extension"
$applyScript = Join-Path $repoRoot "scripts\apply-founder-customizations.ps1"
$fastGulpfile = Join-Path $repoRoot "packages\founder-ide\build\founder-fast-gulpfile.js"

if (-not $CacheRoot -and $env:FOUNDER_IDE_DEV_CACHE) {
    $CacheRoot = $env:FOUNDER_IDE_DEV_CACHE
}
if (-not $CacheRoot) {
    $CacheRoot = Join-Path ([System.IO.Path]::GetTempPath()) "FounderIDE\dev-cache"
}
$CacheRoot = [System.IO.Path]::GetFullPath($CacheRoot)

$overlayFiles = @($manifestPath, $applyScript, $fastGulpfile)
foreach ($entry in $manifest.files) {
    $overlayFiles += Join-Path $overlayRoot ([string]$entry.src)
}
$overlayHash = Get-CompositeHash -Paths $overlayFiles -BasePath $repoRoot

$extensionFiles = @(
    (Join-Path $extensionRoot "package.json"),
    (Join-Path $extensionRoot "tsconfig.json")
)
$extensionFiles += Get-ChildItem -LiteralPath (Join-Path $extensionRoot "src") -Recurse -File |
    Select-Object -ExpandProperty FullName
$extensionFiles += Get-ChildItem -LiteralPath (Join-Path $extensionRoot "resources") -Recurse -File |
    Select-Object -ExpandProperty FullName
$extensionHash = Get-CompositeHash -Paths $extensionFiles -BasePath $repoRoot
$nodeExecutable = Resolve-NodeExecutable
$stageRoot = Join-Path $CacheRoot "extensions\$extensionHash"
$stageReady = Join-Path $stageRoot ".founder-extension-ready.json"

if (-not $CheckoutPath -and $env:FOUNDER_IDE_CHECKOUT) {
    $CheckoutPath = $env:FOUNDER_IDE_CHECKOUT
}
if ($CheckoutPath) {
    $CheckoutPath = [System.IO.Path]::GetFullPath($CheckoutPath)
}

$status = [ordered]@{
    mode = $Mode
    repoRoot = $repoRoot
    overlayHash = $overlayHash
    overlayFiles = $overlayFiles.Count
    extensionHash = $extensionHash
    extensionFiles = $extensionFiles.Count
    extensionStage = $stageRoot
    extensionStageReady = (Test-Path $stageReady)
    checkout = $CheckoutPath
    expectedUpstreamCommit = [string]$manifest.upstream.commit
    expectedNode = [string]$manifest.toolchain_pins.node
    nodeExecutable = $nodeExecutable
    nodeVersion = (& $nodeExecutable --version).Trim()
}

if ($Mode -eq "Status") {
    $status | ConvertTo-Json -Depth 4
    exit 0
}

New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null

$readyFile = Copy-ExtensionStage `
    -ExtensionRoot $extensionRoot `
    -RepoRoot $repoRoot `
    -StageRoot $stageRoot `
    -NodeExecutable $nodeExecutable `
    -ExtensionHash $extensionHash
$status.extensionStageReady = $true
$status.extensionBuild = Get-Content -LiteralPath $readyFile -Raw | ConvertFrom-Json

if ($Mode -eq "Extension") {
    if (-not $NoLaunch) {
        if (-not $InstalledExe) {
            $InstalledExe = Join-Path $env:LOCALAPPDATA "Programs\Founder IDE\Founder IDE.exe"
        }
        if (-not (Test-Path -LiteralPath $InstalledExe -PathType Leaf)) {
            throw "Installed Founder IDE was not found at $InstalledExe"
        }

        $profileRoot = Join-Path $CacheRoot "profiles\extension-$extensionHash"
        $extensionsDir = Join-Path $CacheRoot "profiles\extensions"
        New-Item -ItemType Directory -Force -Path $profileRoot, $extensionsDir | Out-Null
        Start-Process -FilePath $InstalledExe -ArgumentList @(
            (Format-NamedArgument "--user-data-dir" $profileRoot),
            (Format-NamedArgument "--extensions-dir" $extensionsDir),
            (Format-NamedArgument "--extensionDevelopmentPath" $stageRoot)
        )
        $status.launched = $InstalledExe
        $status.profile = $profileRoot
    }
    $status | ConvertTo-Json -Depth 6
    exit 0
}

if (-not $CheckoutPath) {
    throw "Workbench mode requires -CheckoutPath or FOUNDER_IDE_CHECKOUT."
}
Assert-PinnedCheckout `
    -Checkout $CheckoutPath `
    -ExpectedCommit ([string]$manifest.upstream.commit) `
    -ExpectedNode ([string]$manifest.toolchain_pins.node) `
    -NodeExecutable $nodeExecutable

$warmMain = Join-Path $CheckoutPath "out\vs\code\electron-main\main.js"
if (-not (Test-Path -LiteralPath $warmMain -PathType Leaf)) {
    throw "Workbench mode requires an existing compiled output at $warmMain. Run one full compile only when the upstream base changes."
}

$workbenchStateFile = Join-Path $CacheRoot "workbench-state.json"
$previousState = $null
if (Test-Path $workbenchStateFile) {
    $previousState = Get-Content -LiteralPath $workbenchStateFile -Raw | ConvertFrom-Json
}
if ($Force -or -not $previousState -or $previousState.overlayHash -ne $overlayHash) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $applyScript `
        -VscodiumCheckout $CheckoutPath `
        -MonorepoRoot $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Founder overlay application failed with exit code $LASTEXITCODE"
    }
}

$logRoot = Join-Path $CacheRoot "logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$gulpCli = Join-Path $CheckoutPath "node_modules\gulp\bin\gulp.js"
if (-not (Test-Path $gulpCli)) {
    throw "Warm checkout dependencies are missing: $gulpCli"
}

$env:FOUNDER_IDE_CHECKOUT = $CheckoutPath
$watchOut = Join-Path $logRoot "workbench-watch.out.log"
$watchErr = Join-Path $logRoot "workbench-watch.err.log"
$watchProcess = Start-Process `
    -FilePath $nodeExecutable `
    -ArgumentList @("--max-old-space-size=8192", $gulpCli, "--gulpfile", $fastGulpfile, "founder-watch-client") `
    -WorkingDirectory $CheckoutPath `
    -WindowStyle Hidden `
    -RedirectStandardOutput $watchOut `
    -RedirectStandardError $watchErr `
    -PassThru

$reactBuild = Join-Path $CheckoutPath "src\vs\workbench\contrib\void\browser\react\build.js"
$reactOut = Join-Path $logRoot "react-watch.out.log"
$reactErr = Join-Path $logRoot "react-watch.err.log"
$reactProcess = Start-Process `
    -FilePath $nodeExecutable `
    -ArgumentList @($reactBuild, "--watch") `
    -WorkingDirectory (Split-Path -Parent $reactBuild) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $reactOut `
    -RedirectStandardError $reactErr `
    -PassThru

$state = [ordered]@{
    overlayHash = $overlayHash
    extensionHash = $extensionHash
    checkout = $CheckoutPath
    upstreamCommit = [string]$manifest.upstream.commit
    node = (& $nodeExecutable --version).Trim()
    startedAt = (Get-Date).ToUniversalTime().ToString("o")
    workbenchWatchPid = $watchProcess.Id
    reactWatchPid = $reactProcess.Id
    logs = $logRoot
}
$state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $workbenchStateFile -Encoding UTF8
$status.workbench = $state

if (-not $NoLaunch) {
    $codeBat = Join-Path $CheckoutPath "scripts\code.bat"
    if (-not (Test-Path $codeBat)) {
        throw "Unpacked Founder IDE launcher is missing: $codeBat"
    }
    $profileRoot = Join-Path $CacheRoot "profiles\workbench-$overlayHash"
    $extensionsDir = Join-Path $CacheRoot "profiles\extensions"
    New-Item -ItemType Directory -Force -Path $profileRoot, $extensionsDir | Out-Null
    Start-Process -FilePath $codeBat -ArgumentList @(
        (Format-NamedArgument "--user-data-dir" $profileRoot),
        (Format-NamedArgument "--extensions-dir" $extensionsDir),
        (Format-NamedArgument "--extensionDevelopmentPath" $stageRoot)
    ) -WorkingDirectory $CheckoutPath
    $status.launched = $codeBat
    $status.profile = $profileRoot
}

$status | ConvertTo-Json -Depth 6
