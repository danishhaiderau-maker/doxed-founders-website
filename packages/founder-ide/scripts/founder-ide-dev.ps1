[CmdletBinding()]
param(
    [ValidateSet("Status", "Extension", "Workbench")]
    [string]$Mode = "Status",

    [string]$CheckoutPath = "",
    [string]$CacheRoot = "",
    [string]$NodePath = "",
    [string]$TscPath = "",
    [string]$InstalledExe = "",

    [ValidateRange(1, 120)]
    [int]$WorkbenchReadyTimeoutMinutes = 45,

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

function Format-PositionalArgument {
    param(
        [Parameter(Mandatory = $true)][string]$Value
    )
    return '"{0}"' -f $Value.Replace('"', '\"')
}

function Start-PinnedNodeProcess {
    param(
        [Parameter(Mandatory = $true)][string]$NodeExecutable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StandardOutput,
        [Parameter(Mandatory = $true)][string]$StandardError
    )

    # build.js launches npx children. Put the pinned runtime first so those
    # children cannot silently inherit a different system Node installation.
    $previousPath = $env:PATH
    $nodeDirectory = Split-Path -Parent $NodeExecutable
    try {
        $env:PATH = "$nodeDirectory;$previousPath"
        return Start-Process `
            -FilePath $NodeExecutable `
            -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory `
            -WindowStyle Hidden `
            -RedirectStandardOutput $StandardOutput `
            -RedirectStandardError $StandardError `
            -PassThru
    } finally {
        $env:PATH = $previousPath
    }
}

function Get-RecordedNodeProcess {
    param(
        [int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$ExpectedNode,
        [Parameter(Mandatory = $true)][DateTime]$NotBefore
    )

    if ($ProcessId -le 0) {
        return $null
    }
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process -or $process.HasExited) {
        return $null
    }
    try {
        $actualPath = [System.IO.Path]::GetFullPath($process.Path)
        $expectedPath = [System.IO.Path]::GetFullPath($ExpectedNode)
        if (-not $actualPath.Equals($expectedPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
        if ($process.StartTime.ToUniversalTime() -lt $NotBefore.ToUniversalTime().AddMinutes(-1)) {
            return $null
        }
    } catch {
        return $null
    }
    return $process
}

function Get-LogTextSince {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [long]$Offset = 0
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    $text = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if (-not $text) {
        return ""
    }
    if ($Offset -le 0) {
        return $text
    }
    if ($Offset -gt $text.Length) {
        return $text
    }
    if ($Offset -eq $text.Length) {
        return ""
    }
    return $text.Substring([int]$Offset)
}

function Remove-AnsiEscapeSequences {
    param([string]$Text)

    if (-not $Text) {
        return ""
    }
    return [regex]::Replace($Text, "$([char]27)\[[0-9;]*[A-Za-z]", "")
}

function Get-ExpectedWorkbenchOutputs {
    param(
        [Parameter(Mandatory = $true)][string]$Checkout,
        [Parameter(Mandatory = $true)][string[]]$CompileInputs
    )

    foreach ($relativePath in $CompileInputs) {
        $normalized = $relativePath.Replace("/", "\")
        if (
            -not $normalized.StartsWith("src\", [System.StringComparison]::OrdinalIgnoreCase) -or
            $normalized -match "\\browser\\react\\src\\" -or
            $normalized.EndsWith(".d.ts", [System.StringComparison]::OrdinalIgnoreCase)
        ) {
            continue
        }
        $extension = [System.IO.Path]::GetExtension($normalized)
        if ($extension -notin @(".ts", ".tsx")) {
            continue
        }
        $outputRelative = "out\" + $normalized.Substring(4)
        $outputRelative = [System.IO.Path]::ChangeExtension($outputRelative, ".js")
        [pscustomobject]@{
            source = Join-Path $Checkout $normalized
            output = Join-Path $Checkout $outputRelative
        }
    }
}

function Get-CheckoutCompileInputs {
    param([Parameter(Mandatory = $true)][string]$Checkout)

    $inputs = @(
        & git -c "safe.directory=$Checkout" -C $Checkout diff --name-only -- `
            "src/**/*.ts" `
            "src/**/*.tsx"
    )
    $inputs += @(
        & git -c "safe.directory=$Checkout" -C $Checkout ls-files --others --exclude-standard -- `
            "src/**/*.ts" `
            "src/**/*.tsx"
    )
    return @($inputs | Sort-Object -Unique)
}

function Get-CompileInputDigests {
    param(
        [Parameter(Mandatory = $true)][string]$Checkout,
        [Parameter(Mandatory = $true)][string[]]$CompileInputs
    )

    $digests = @{}
    foreach ($relativePath in $CompileInputs) {
        $path = Join-Path $Checkout $relativePath
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $digests[$relativePath] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        }
    }
    return $digests
}

function Get-ChangedCompileInputs {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Before,
        [Parameter(Mandatory = $true)][hashtable]$After
    )

    $paths = @($Before.Keys) + @($After.Keys) | Sort-Object -Unique
    return @(
        $paths | Where-Object {
            -not $Before.ContainsKey($_) -or
            -not $After.ContainsKey($_) -or
            $Before[$_] -ne $After[$_]
        }
    )
}

function Test-WorkbenchOutputsCurrent {
    param(
        [Parameter(Mandatory = $true)][object[]]$ExpectedOutputs
    )

    foreach ($entry in $ExpectedOutputs) {
        if (
            -not (Test-Path -LiteralPath $entry.source -PathType Leaf) -or
            -not (Test-Path -LiteralPath $entry.output -PathType Leaf)
        ) {
            return $false
        }
        $sourceTime = (Get-Item -LiteralPath $entry.source).LastWriteTimeUtc
        $outputTime = (Get-Item -LiteralPath $entry.output).LastWriteTimeUtc
        if ($outputTime -lt $sourceTime) {
            return $false
        }
    }
    return $true
}

function Get-ExpectedReactOutputs {
    param(
        [Parameter(Mandatory = $true)][string]$Checkout,
        [Parameter(Mandatory = $true)][string[]]$CompileInputs
    )

    $reactRoot = "src\vs\workbench\contrib\void\browser\react\src\"
    foreach ($relativePath in $CompileInputs) {
        $normalized = $relativePath.Replace("/", "\")
        if (-not $normalized.StartsWith($reactRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $remainder = $normalized.Substring($reactRoot.Length)
        $bundleName = $remainder.Split("\")[0]
        if (-not $bundleName) {
            continue
        }
        [pscustomobject]@{
            source = Join-Path $Checkout $normalized
            output = Join-Path $Checkout "src\vs\workbench\contrib\void\browser\react\out\$bundleName\index.js"
        }
    }
}

function Get-ExpectedBuiltInExtensionOutputs {
    param(
        [Parameter(Mandatory = $true)][string]$Checkout
    )

    @(
        [pscustomobject]@{
            source = Join-Path $Checkout "extensions\git-base\src\extension.ts"
            output = Join-Path $Checkout "extensions\git-base\out\extension.js"
        },
        [pscustomobject]@{
            source = Join-Path $Checkout "extensions\git\src\main.ts"
            output = Join-Path $Checkout "extensions\git\out\main.js"
        }
    )
}

function Assert-BuiltInExtensionDependencies {
    param([Parameter(Mandatory = $true)][string]$Checkout)

    $required = @(
        "extensions\git-base\node_modules\@types\node\package.json",
        "extensions\git\node_modules\@vscode\extension-telemetry\package.json",
        "extensions\emmet\node_modules\@emmetio\html-matcher\package.json",
        "extensions\css-language-features\node_modules\vscode-languageclient\package.json",
        "extensions\css-language-features\server\node_modules\vscode-languageserver\package.json",
        "extensions\github-authentication\node_modules\@vscode\extension-telemetry\package.json",
        "extensions\html-language-features\node_modules\vscode-languageclient\package.json",
        "extensions\html-language-features\server\node_modules\vscode-languageserver\package.json",
        "extensions\ipynb\node_modules\@enonic\fnv-plus\package.json",
        "extensions\json-language-features\node_modules\vscode-languageclient\package.json",
        "extensions\json-language-features\server\node_modules\vscode-languageserver\package.json",
        "extensions\markdown-language-features\node_modules\vscode-markdown-languageserver\package.json",
        "extensions\markdown-math\node_modules\@vscode\markdown-it-katex\package.json",
        "extensions\media-preview\node_modules\@vscode\extension-telemetry\package.json",
        "extensions\merge-conflict\node_modules\@vscode\extension-telemetry\package.json",
        "extensions\microsoft-authentication\node_modules\@azure\msal-node-extensions\package.json",
        "extensions\npm\node_modules\request-light\package.json",
        "extensions\github\node_modules\@vscode\extension-telemetry\package.json",
        "extensions\simple-browser\node_modules\@vscode\extension-telemetry\package.json",
        "extensions\typescript-language-features\node_modules\@vscode\extension-telemetry\package.json",
        "extensions\open-remote-ssh\node_modules\ssh2\package.json"
    )
    foreach ($relativePath in $required) {
        $path = Join-Path $Checkout $relativePath
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Warm checkout dependency is missing: $relativePath. Run locked npm ci in the named built-in extension directory once."
        }
    }
}

function Wait-ForTypeScriptCompile {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$OutputLog,
        [Parameter(Mandatory = $true)][string]$ErrorLog,
        [Parameter(Mandatory = $true)][long]$OutputOffset,
        [Parameter(Mandatory = $true)][object[]]$ExpectedOutputs,
        [Parameter(Mandatory = $true)][string]$CompilerName,
        [Parameter(Mandatory = $true)][TimeSpan]$Timeout
    )

    $deadline = [DateTime]::UtcNow.Add($Timeout)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            $stderr = Get-LogTextSince -Path $ErrorLog
            throw "$CompilerName compiler exited before becoming ready (exit $($Process.ExitCode)). $stderr"
        }

        $newLog = Remove-AnsiEscapeSequences (
            Get-LogTextSince -Path $OutputLog -Offset $OutputOffset
        )
        $mainCompileLines = @(
            $newLog -split "\r?\n" |
                Where-Object {
                    $_ -match "Finished compilation with \d+ errors? after" -and
                    $_ -notmatch "api-proposal-names"
                }
        )
        $failedCompile = $mainCompileLines | Where-Object {
            $_ -notmatch "Finished compilation with 0 errors? after"
        } | Select-Object -First 1
        if ($failedCompile) {
            throw "$CompilerName compilation failed: $failedCompile"
        }
        if (
            $mainCompileLines.Count -gt 0 -and
            (Test-WorkbenchOutputsCurrent -ExpectedOutputs $ExpectedOutputs)
        ) {
            return [DateTime]::UtcNow
        }
        Start-Sleep -Seconds 2
    }

    $stderr = Get-LogTextSince -Path $ErrorLog
    throw "$CompilerName did not reach a verified compiler checkpoint within $([int]$Timeout.TotalMinutes) minutes. See $OutputLog and $ErrorLog. $stderr"
}

function Wait-ForReactCompile {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$OutputLog,
        [Parameter(Mandatory = $true)][string]$ErrorLog,
        [Parameter(Mandatory = $true)][long]$OutputOffset,
        [Parameter(Mandatory = $true)][object[]]$ExpectedOutputs,
        [Parameter(Mandatory = $true)][TimeSpan]$Timeout
    )

    $deadline = [DateTime]::UtcNow.Add($Timeout)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            $stderr = Get-LogTextSince -Path $ErrorLog
            throw "Founder React compiler exited before becoming ready (exit $($Process.ExitCode)). $stderr"
        }

        $newLog = Remove-AnsiEscapeSequences (
            Get-LogTextSince -Path $OutputLog -Offset $OutputOffset
        )
        if (
            $newLog -match "Build success in \d+ms" -and
            (Test-WorkbenchOutputsCurrent -ExpectedOutputs $ExpectedOutputs)
        ) {
            return [DateTime]::UtcNow
        }
        Start-Sleep -Seconds 2
    }

    $stderr = Get-LogTextSince -Path $ErrorLog
    throw "Founder React UI did not reach a verified bundle checkpoint within $([int]$Timeout.TotalMinutes) minutes. See $OutputLog and $ErrorLog. $stderr"
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
        [string]$TypeScriptCompiler = "",
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

    if (-not $TypeScriptCompiler -and $env:FOUNDER_IDE_TSC) {
        $TypeScriptCompiler = $env:FOUNDER_IDE_TSC
    }
    $tscCandidates = @(
        $TypeScriptCompiler,
        (Join-Path $ExtensionRoot "node_modules\typescript\bin\tsc"),
        (Join-Path $RepoRoot "node_modules\typescript\bin\tsc")
    ) | Where-Object { $_ }
    $tsc = $tscCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $tsc) {
        throw "TypeScript compiler not found. Install workspace dependencies or pass -TscPath / FOUNDER_IDE_TSC."
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

$overlayContentFiles = @($manifestPath, $applyScript)
foreach ($entry in $manifest.files) {
    $overlayContentFiles += Join-Path $overlayRoot ([string]$entry.src)
}
$workflowFiles = @($fastGulpfile, $PSCommandPath)
$overlayFiles = @($overlayContentFiles) + @($workflowFiles)
$overlayContentHash = Get-CompositeHash -Paths $overlayContentFiles -BasePath $repoRoot
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
    overlayContentHash = $overlayContentHash
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
    typescriptCompiler = $TscPath
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
    -TypeScriptCompiler $TscPath `
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
$compileInputsBefore = @(Get-CheckoutCompileInputs -Checkout $CheckoutPath)
$compileDigestsBefore = Get-CompileInputDigests `
    -Checkout $CheckoutPath `
    -CompileInputs $compileInputsBefore
$overlayApplied = $false
if (
    $Force -or
    -not $previousState -or
    -not $previousState.overlayContentHash -or
    $previousState.overlayContentHash -ne $overlayContentHash
) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $applyScript `
        -VscodiumCheckout $CheckoutPath `
        -MonorepoRoot $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Founder overlay application failed with exit code $LASTEXITCODE"
    }
    $overlayApplied = $true
}

$logRoot = Join-Path $CacheRoot "logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$gulpCli = Join-Path $CheckoutPath "node_modules\gulp\bin\gulp.js"
if (-not (Test-Path $gulpCli)) {
    throw "Warm checkout dependencies are missing: $gulpCli"
}
$checkoutGulpfile = Join-Path $CheckoutPath ".founder-fast-gulpfile.cjs"
Copy-Item -LiteralPath $fastGulpfile -Destination $checkoutGulpfile -Force

$env:FOUNDER_IDE_CHECKOUT = $CheckoutPath
$watchOut = Join-Path $logRoot "workbench-watch.out.log"
$watchErr = Join-Path $logRoot "workbench-watch.err.log"
$watchLogOffset = if (Test-Path -LiteralPath $watchOut -PathType Leaf) {
    (Get-Content -LiteralPath $watchOut -Raw -ErrorAction SilentlyContinue).Length
} else {
    0
}
$reactOut = Join-Path $logRoot "react-watch.out.log"
$reactErr = Join-Path $logRoot "react-watch.err.log"
$reactLogOffset = if (Test-Path -LiteralPath $reactOut -PathType Leaf) {
    (Get-Content -LiteralPath $reactOut -Raw -ErrorAction SilentlyContinue).Length
} else {
    0
}
$extensionsOut = Join-Path $logRoot "extensions-build.out.log"
$extensionsErr = Join-Path $logRoot "extensions-build.err.log"
$canReusePrevious = $previousState `
    -and $previousState.checkout -eq $CheckoutPath `
    -and $previousState.upstreamCommit -eq [string]$manifest.upstream.commit `
    -and $previousState.node -eq (& $nodeExecutable --version).Trim()
$watchProcess = if ($canReusePrevious) {
    $watchStartedAt = if ($previousState.workbenchWatchStartedAt) {
        [DateTime]::Parse([string]$previousState.workbenchWatchStartedAt)
    } else {
        [DateTime]::Parse([string]$previousState.startedAt).AddHours(-1)
    }
    Get-RecordedNodeProcess `
        -ProcessId ([int]$previousState.workbenchWatchPid) `
        -ExpectedNode $nodeExecutable `
        -NotBefore $watchStartedAt
}
if (-not $watchProcess) {
    $watchProcess = Start-PinnedNodeProcess `
        -NodeExecutable $nodeExecutable `
        -Arguments @(
            "--max-old-space-size=8192",
            (Format-PositionalArgument $gulpCli),
            "--gulpfile",
            (Format-PositionalArgument $checkoutGulpfile),
            "founder-watch-client"
        ) `
        -WorkingDirectory $CheckoutPath `
        -StandardOutput $watchOut `
        -StandardError $watchErr
}

$reactBuild = Join-Path $CheckoutPath "src\vs\workbench\contrib\void\browser\react\build.js"
$reactProcess = if ($canReusePrevious) {
    $reactStartedAt = if ($previousState.reactWatchStartedAt) {
        [DateTime]::Parse([string]$previousState.reactWatchStartedAt)
    } else {
        [DateTime]::Parse([string]$previousState.startedAt).AddHours(-1)
    }
    Get-RecordedNodeProcess `
        -ProcessId ([int]$previousState.reactWatchPid) `
        -ExpectedNode $nodeExecutable `
        -NotBefore $reactStartedAt
}
if (-not $reactProcess) {
    $reactProcess = Start-PinnedNodeProcess `
        -NodeExecutable $nodeExecutable `
        -Arguments @((Format-PositionalArgument $reactBuild), "--watch") `
        -WorkingDirectory (Split-Path -Parent $reactBuild) `
        -StandardOutput $reactOut `
        -StandardError $reactErr
}

$compileInputs = @(Get-CheckoutCompileInputs -Checkout $CheckoutPath)
$compileDigestsAfter = Get-CompileInputDigests `
    -Checkout $CheckoutPath `
    -CompileInputs $compileInputs
$changedCompileInputs = if ($overlayApplied) {
    @(
        Get-ChangedCompileInputs `
            -Before $compileDigestsBefore `
            -After $compileDigestsAfter
    )
} else {
    @()
}
$changedWorkbenchInputs = @(
    $changedCompileInputs | Where-Object {
        $_.Replace("/", "\") -notmatch "\\browser\\react\\src\\"
    }
)
$changedReactInputs = @(
    $changedCompileInputs | Where-Object {
        $_.Replace("/", "\") -match "\\browser\\react\\src\\"
    }
)

$workbenchCompileReadyAt = if ($previousState -and $previousState.workbenchCompileReadyAt) {
    [DateTime]::Parse([string]$previousState.workbenchCompileReadyAt).ToUniversalTime()
} else {
    $null
}
$compileRequired = $changedWorkbenchInputs.Count -gt 0 -or -not $workbenchCompileReadyAt
if ($compileRequired) {
    $expectedOutputs = @(
        Get-ExpectedWorkbenchOutputs `
            -Checkout $CheckoutPath `
            -CompileInputs $compileInputs
    )
    if ($expectedOutputs.Count -eq 0) {
        throw "Founder workbench readiness could not be verified because no compiled overlay outputs were identified."
    }
    $existingWorkbenchLog = Remove-AnsiEscapeSequences (
        Get-LogTextSince -Path $watchOut
    )
    $latestWorkbenchCompile = @(
        $existingWorkbenchLog -split "\r?\n" |
            Where-Object {
                $_ -match "Finished compilation with \d+ errors? after" -and
                $_ -notmatch "api-proposal-names"
            }
    ) | Select-Object -Last 1
    if (
        $latestWorkbenchCompile -match "Finished compilation with 0 errors? after" -and
        (Test-WorkbenchOutputsCurrent -ExpectedOutputs $expectedOutputs)
    ) {
        $workbenchCompileReadyAt = [DateTime]::UtcNow
    } else {
        $workbenchCompileReadyAt = Wait-ForTypeScriptCompile `
            -Process $watchProcess `
            -OutputLog $watchOut `
            -ErrorLog $watchErr `
            -OutputOffset $watchLogOffset `
            -ExpectedOutputs $expectedOutputs `
            -CompilerName "Founder workbench" `
            -Timeout ([TimeSpan]::FromMinutes($WorkbenchReadyTimeoutMinutes))
    }
}

$reactCompileReadyAt = if ($previousState -and $previousState.reactCompileReadyAt) {
    [DateTime]::Parse([string]$previousState.reactCompileReadyAt).ToUniversalTime()
} else {
    $null
}
$reactExpectedInputs = if ($changedReactInputs.Count -gt 0) {
    $changedReactInputs
} else {
    $compileInputs
}
$expectedReactOutputs = @(
    Get-ExpectedReactOutputs `
        -Checkout $CheckoutPath `
        -CompileInputs $reactExpectedInputs
)
$reactCompileRequired = $changedReactInputs.Count -gt 0 -or -not $reactCompileReadyAt
if ($reactCompileRequired) {
    $existingReactLog = Remove-AnsiEscapeSequences (
        Get-LogTextSince -Path $reactOut
    )
    if (
        $existingReactLog -match "Build success in \d+ms" -and
        (Test-WorkbenchOutputsCurrent -ExpectedOutputs $expectedReactOutputs)
    ) {
        $reactCompileReadyAt = [DateTime]::UtcNow
    } elseif ($expectedReactOutputs.Count -eq 0) {
        throw "Founder React readiness could not be verified because no overlay bundles were identified."
    } else {
        $reactCompileReadyAt = Wait-ForReactCompile `
            -Process $reactProcess `
            -OutputLog $reactOut `
            -ErrorLog $reactErr `
            -OutputOffset $reactLogOffset `
            -ExpectedOutputs $expectedReactOutputs `
            -Timeout ([TimeSpan]::FromMinutes($WorkbenchReadyTimeoutMinutes))
    }
}

$expectedBuiltInExtensionOutputs = @(
    Get-ExpectedBuiltInExtensionOutputs -Checkout $CheckoutPath
)
$extensionCompileReadyAt = if ($previousState -and $previousState.extensionCompileReadyAt) {
    [DateTime]::Parse([string]$previousState.extensionCompileReadyAt).ToUniversalTime()
} else {
    $null
}
Assert-BuiltInExtensionDependencies -Checkout $CheckoutPath
if (Test-WorkbenchOutputsCurrent -ExpectedOutputs $expectedBuiltInExtensionOutputs) {
    if (-not $extensionCompileReadyAt) {
        $extensionCompileReadyAt = [DateTime]::UtcNow
    }
} else {
    $extensionsProcess = Start-PinnedNodeProcess `
        -NodeExecutable $nodeExecutable `
        -Arguments @(
            "--max-old-space-size=8192",
            "node_modules\gulp\bin\gulp.js",
            "compile-extension:git-base",
            "compile-extension:git"
        ) `
        -WorkingDirectory $CheckoutPath `
        -StandardOutput $extensionsOut `
        -StandardError $extensionsErr
    if (-not $extensionsProcess.WaitForExit([int]([TimeSpan]::FromMinutes($WorkbenchReadyTimeoutMinutes).TotalMilliseconds))) {
        Stop-Process -Id $extensionsProcess.Id -Force -ErrorAction SilentlyContinue
        throw "Founder built-in extension compilation timed out. See $extensionsOut and $extensionsErr."
    }
    if ($extensionsProcess.ExitCode -ne 0) {
        $stderr = Get-LogTextSince -Path $extensionsErr
        throw "Founder built-in extension compilation failed (exit $($extensionsProcess.ExitCode)). $stderr"
    }
    if (-not (Test-WorkbenchOutputsCurrent -ExpectedOutputs $expectedBuiltInExtensionOutputs)) {
        throw "Founder built-in extension compilation completed without current Git and Git Base outputs."
    }
    $extensionCompileReadyAt = [DateTime]::UtcNow
}

$stateUpdatedAt = (Get-Date).ToUniversalTime()
$state = [ordered]@{
    overlayHash = $overlayHash
    overlayContentHash = $overlayContentHash
    extensionHash = $extensionHash
    checkout = $CheckoutPath
    upstreamCommit = [string]$manifest.upstream.commit
    node = (& $nodeExecutable --version).Trim()
    startedAt = $stateUpdatedAt.ToString("o")
    workbenchWatchPid = $watchProcess.Id
    workbenchWatchStartedAt = $watchProcess.StartTime.ToUniversalTime().ToString("o")
    workbenchCompileReadyAt = $workbenchCompileReadyAt.ToString("o")
    reactWatchPid = $reactProcess.Id
    reactWatchStartedAt = $reactProcess.StartTime.ToUniversalTime().ToString("o")
    reactCompileReadyAt = $reactCompileReadyAt.ToString("o")
    extensionCompileReadyAt = $extensionCompileReadyAt.ToString("o")
    announcedCompileInputs = $compileInputs.Count
    changedCompileInputs = $changedCompileInputs.Count
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
