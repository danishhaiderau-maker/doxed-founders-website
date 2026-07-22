# scripts/apply-founder-customizations.ps1
#
# Layers the Founder IDE customizations on top of a fresh upstream Void
# (voideditor/void) checkout, so CI can build Founder IDE reproducibly.
#
# Run it from the MONOREPO ROOT. It expects:
#   - packages/founder-ide/upstream/overlay/             (this repo - the overlay files)
#   - packages/founder-ide/upstream/overlay/MANIFEST.json (overlay file map + upstream pin)
#   - $VscodiumCheckout (env or -VscodiumCheckout)        (the freshly cloned Void/vscode dir)
#
# What it does:
#   1. Validates the checkout is at the pinned upstream commit (advisory only - does NOT
#      abort, so a security-patched fork at a slightly newer SHA can still build).
#   2. Reads MANIFEST.json and copies each overlay file onto the checkout:
#        mode:"replace" -> FileExists must be true upstream, else error
#        mode:"add"     -> FileExists must be false upstream, else error
#   3. Verifies the patched product.json parses and that the Founder IDE branding
#      fields landed (nameLong, applicationName, win32x64UserAppId).
#   4. Verifies the Gateway rewire touched sendLLMMessage.ts (looks for the
#      FOUNDER_OS_GATEWAY_REWIRE marker).
#
# Why this design (rather than a .patch file):
#   - Void's checkout on Windows flips LF->CRLF, which breaks line-anchored patches.
#   - One customization is a brand-new file (sendFounderOs.ts), which a patch can't add
#     cleanly without binary mode.
#   - Verbatim file copies are trivial to diff/review and survive line-ending churn.
#
# Reuses: this script is called by .github/workflows/build-founder-ide.yml. It is
# idempotent - running it twice produces the same result.

[CmdletBinding()]
param(
    # Path to the freshly cloned Void checkout's vscode/ subdirectory.
    # Default: $env:VSCODE_CHECKOUT or "./vscode" relative to cwd.
    [string]$VscodiumCheckout = $(if ($env:VSCODE_CHECKOUT) { $env:VSCODE_CHECKOUT } else { (Join-Path (Get-Location) "vscode") }),

    # Path to the monorepo root (where packages/founder-ide/upstream/overlay lives).
    # Default: detected by walking up from this script until a `packages/` dir is found.
    [string]$MonorepoRoot = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

# --- Locate the monorepo root ------------------------------------------------
if (-not $MonorepoRoot) {
    $p = Split-Path -Parent $MyInvocation.MyCommand.Path
    for ($i = 0; $i -lt 6 -and $p; $i++) {
        if (Test-Path (Join-Path $p "packages\founder-ide\upstream\overlay\MANIFEST.json")) {
            $MonorepoRoot = $p; break
        }
        $p = Split-Path -Parent $p
    }
    if (-not $MonorepoRoot) {
        throw "Could not locate monorepo root (no packages/founder-ide/upstream/overlay/MANIFEST.json walking up from script)."
    }
}

$overlayDir  = Join-Path $MonorepoRoot "packages\founder-ide\upstream\overlay"
$manifestF   = Join-Path $overlayDir "MANIFEST.json"

if (-not (Test-Path $manifestF))   { throw "Overlay MANIFEST.json not found at $manifestF" }
if (-not (Test-Path $VscodiumCheckout)) { throw "Void checkout not found at $VscodiumCheckout (pass -VscodiumCheckout or set VSCODE_CHECKOUT)" }

Write-Host "[apply-founder] monorepo root : $MonorepoRoot"
Write-Host "[apply-founder] overlay dir   : $overlayDir"
Write-Host "[apply-founder] void checkout : $VscodiumCheckout"

# --- Load manifest -----------------------------------------------------------
$manifest = Get-Content $manifestF -Raw | ConvertFrom-Json
$expectedCommit = $manifest.upstream.commit
$expectedSubdir = $manifest.upstream.subdir   # "vscode"

# If the user pointed us at void-builder/ (the meta-repo), descend into vscode/.
$detectedProductJson = Join-Path $VscodiumCheckout "product.json"
if (-not (Test-Path $detectedProductJson) -and (Test-Path (Join-Path $VscodiumCheckout $expectedSubdir "product.json"))) {
    $VscodiumCheckout = Join-Path $VscodiumCheckout $expectedSubdir
    Write-Host "[apply-founder] descended into $expectedSubdir/ -> $VscodiumCheckout"
}

# --- Advisory: verify pinned commit -----------------------------------------
# We don't hard-abort on mismatch so that security-patched forks at a slightly
# newer SHA can still build. We DO log loudly so the build log shows the drift.
$actualCommit = $null
if (Test-Path (Join-Path $VscodiumCheckout ".git")) {
    try {
        $actualCommit = (git -C $VscodiumCheckout rev-parse HEAD 2>$null)
    } catch { $actualCommit = $null }
}
if ($actualCommit) {
    if ($actualCommit -eq $expectedCommit) {
        Write-Host "[apply-founder] upstream commit OK: $actualCommit" -ForegroundColor Green
    } else {
        Write-Warning "[apply-founder] upstream commit DRIFT: expected $expectedCommit, got $actualCommit"
        Write-Warning "[apply-founder] building anyway (pin may need a bump in MANIFEST.json)"
    }
} else {
    Write-Warning "[apply-founder] no .git in checkout - skipping commit verification (continuing)"
}

# --- Apply each overlay file -------------------------------------------------
$copied = 0
foreach ($entry in $manifest.files) {
    $srcRel  = $entry.src
    $destRel = $entry.dest
    $mode    = $entry.mode

    $srcAbs  = Join-Path $overlayDir   $srcRel
    $destAbs = Join-Path $VscodiumCheckout $destRel

    if (-not (Test-Path $srcAbs)) { throw "Overlay source missing: $srcAbs" }

    $destExists = Test-Path $destAbs
    switch ($mode) {
        "replace" {
            if (-not $destExists) {
                throw "mode=replace but upstream file not found at $destAbs (upstream layout changed?)"
            }
        }
        "add" {
            if ($destExists) {
                $marker = [string]$entry.marker
                $existingContent = Get-Content $destAbs -Raw
                if (-not $marker -or $existingContent -notmatch [regex]::Escape($marker)) {
                    throw "mode=add but upstream already has $destAbs without the Founder overlay marker (upstream conflict; review before overwriting)"
                }
                Write-Host "[apply-founder]   reapply  $destRel" -ForegroundColor DarkCyan
            }
        }
        default { throw "Unknown mode '$mode' for $srcRel (expected 'replace' or 'add')" }
    }

    $destParent = Split-Path -Parent $destAbs
    if (-not (Test-Path $destParent)) {
        New-Item -ItemType Directory -Force -Path $destParent | Out-Null
    }
    Copy-Item $srcAbs $destAbs -Force
    $copied++
    Write-Host ("[apply-founder]   {0,-7}  {1}" -f $mode, $destRel) -ForegroundColor Cyan
}
Write-Host "[apply-founder] applied $copied overlay file(s)" -ForegroundColor Green

# The native chat already carries a stable thread id. Add the active workspace
# root to its logging metadata so the Electron-side Founder coordination bridge
# can scope awareness to the correct project without replacing the large
# upstream chatThreadService.ts file in our overlay.
$chatThreadService = Join-Path $VscodiumCheckout "src\vs\workbench\contrib\void\browser\chatThreadService.ts"
if (-not (Test-Path $chatThreadService)) { throw "chatThreadService.ts missing at $chatThreadService" }
$chatThreadContent = Get-Content $chatThreadService -Raw
$coordinationOld = 'logging: { loggingName: `Chat - ${chatMode}`, loggingExtras: { threadId, nMessagesSent, chatMode } },'
$coordinationNew = 'logging: { loggingName: `Chat - ${chatMode}`, loggingExtras: { threadId, nMessagesSent, chatMode, workspacePath: this._workspaceContextService.getWorkspace().folders[0]?.uri.fsPath ?? '''' } },'
if ($chatThreadContent.Contains($coordinationOld)) {
    $chatThreadContent = $chatThreadContent.Replace($coordinationOld, $coordinationNew)
    [System.IO.File]::WriteAllText($chatThreadService, $chatThreadContent, [System.Text.UTF8Encoding]::new($false))
    Write-Host "[apply-founder]   patch    native chat workspace coordination" -ForegroundColor Cyan
} elseif (-not $chatThreadContent.Contains($coordinationNew)) {
    throw "Native chat logging signature changed upstream; workspace coordination was not applied"
} else {
    Write-Host "[apply-founder]   reapply  native chat workspace coordination" -ForegroundColor DarkCyan
}

# Keep user-visible upstream strings behind a small, fail-closed branding
# boundary. Copying the very large upstream modules into the overlay would make
# upgrades needlessly brittle; exact literal replacements instead fail loudly
# when upstream moves while preserving internal service/API identifiers.
function Set-FounderSourceLiteral {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Old,
        [Parameter(Mandatory = $true)][string]$New
    )

    $target = Join-Path $VscodiumCheckout $RelativePath
    if (-not (Test-Path $target)) { throw "Founder branding target missing: $target" }
    $content = Get-Content $target -Raw
    if ($content.Contains($Old)) {
        $content = $content.Replace($Old, $New)
        [System.IO.File]::WriteAllText($target, $content, [System.Text.UTF8Encoding]::new($false))
        Write-Host "[apply-founder]   brand    $RelativePath" -ForegroundColor Cyan
        return
    }
    if (-not $content.Contains($New)) {
        throw "Founder branding signature changed upstream in ${RelativePath}: '$Old'"
    }
    Write-Host "[apply-founder]   reapply  $RelativePath" -ForegroundColor DarkCyan
}

$telemetryService = "src\vs\platform\telemetry\common\telemetryService.ts"
Set-FounderSourceLiteral $telemetryService `
    "Void separately records basic usage like the number of messages people are sending. If you'd like to disable Void metrics, you may do so in Void's Settings." `
    "Founder IDE can record basic product usage such as message counts. You can disable Founder metrics in Founder Settings."

$sendMessageService = "src\vs\workbench\contrib\void\common\sendLLMMessageService.ts"
Set-FounderSourceLiteral $sendMessageService "Please add a provider in Void's Settings." "Choose Founder Managed AI, Personal AI, or Local AI in Founder Settings."

$settingsTypes = "src\vs\workbench\contrib\void\common\voidSettingsTypes.ts"
Set-FounderSourceLiteral $settingsTypes "authenticate before using Vertex with Void." "authenticate before using Vertex with Founder IDE."

$scmService = "src\vs\workbench\contrib\void\browser\voidSCMService.ts"
Set-FounderSourceLiteral $scmService "VoidSCM - Commit Message" "Founder IDE - Commit Message"
Set-FounderSourceLiteral $scmService "Void: Generate Commit Message" "Founder IDE: Generate Commit Message"
Set-FounderSourceLiteral $scmService "Void: Cancel Commit Message Generation" "Founder IDE: Cancel Commit Message Generation"

$commandBarService = "src\vs\workbench\contrib\void\browser\voidCommandBarService.ts"
Set-FounderSourceLiteral $commandBarService "Void: " "Founder IDE: "

$toolsService = "src\vs\workbench\contrib\void\browser\toolsService.ts"
Set-FounderSourceLiteral $toolsService "automatically killed by Void after" "automatically stopped by Founder IDE after"

$terminalService = "src\vs\workbench\contrib\void\browser\terminalToolService.ts"
Set-FounderSourceLiteral $terminalService "Void Agent" "Founder Agent"

$sidebarPane = "src\vs\workbench\contrib\void\browser\sidebarPane.ts"
Set-FounderSourceLiteral $sidebarPane "Open Void Sidebar" "Open Founder Chat"

$editCodeService = "src\vs\workbench\contrib\void\browser\editCodeService.ts"
Set-FounderSourceLiteral $editCodeService "Void Agent" "Founder Agent"
Set-FounderSourceLiteral $editCodeService "Void:" "Founder IDE:"

$fileService = "src\vs\workbench\contrib\void\browser\fileService.ts"
Set-FounderSourceLiteral $fileService "Void: Copy Prompt" "Founder IDE: Copy Prompt"

$metricsService = "src\vs\workbench\contrib\void\common\metricsService.ts"
Set-FounderSourceLiteral $metricsService "Void: Log Debug Info" "Founder IDE: Log Debug Info"

$quickEditActions = "src\vs\workbench\contrib\void\browser\quickEditActions.ts"
Set-FounderSourceLiteral $quickEditActions "Void: Quick Edit" "Founder IDE: Quick Edit"

$legacyOnboarding = "src\vs\workbench\contrib\void\browser\react\src\void-onboarding\VoidOnboarding.tsx"
Set-FounderSourceLiteral $legacyOnboarding `
    "[Email us](mailto:founders@voideditor.com)" `
    "[Founder support](https://doxxedcrypto.digital)"

$providerRuntime = "src\vs\workbench\contrib\void\electron-main\llmMessage\sendLLMMessage.impl.ts"
Set-FounderSourceLiteral $providerRuntime "'HTTP-Referer': 'https://voideditor.com'" "'HTTP-Referer': 'https://doxxedcrypto.digital'"
Set-FounderSourceLiteral $providerRuntime "'X-Title': 'Void'" "'X-Title': 'Founder IDE'"
Set-FounderSourceLiteral $providerRuntime "Void providerName was invalid" "Founder IDE provider name was invalid"
Set-FounderSourceLiteral $providerRuntime "Void: Response from model was empty." "Founder AI response was empty."

# Founder Node owns the signed manifest, verification, rollback, and Dragon
# update states. Disable Void's legacy action and periodic checker so the one
# installed application never exposes or runs a second update path.
$legacyUpdater = "src\vs\workbench\contrib\void\browser\voidUpdateActions.ts"
Set-FounderSourceLiteral $legacyUpdater "registerAction2(class extends Action2" "false && registerAction2(class extends Action2"
Set-FounderSourceLiteral $legacyUpdater "registerWorkbenchContribution2(VoidUpdateWorkbenchContribution.ID" "false && registerWorkbenchContribution2(VoidUpdateWorkbenchContribution.ID"
Set-FounderSourceLiteral $legacyUpdater "This is a very old version of Void" "This Founder IDE build is out of date"
Set-FounderSourceLiteral $legacyUpdater "[Void Editor](https://voideditor.com/download-beta)" "[Founder IDE](https://doxxedcrypto.digital)"
Set-FounderSourceLiteral $legacyUpdater "https://voideditor.com/download-beta" "https://doxxedcrypto.digital"
Set-FounderSourceLiteral $legacyUpdater "Void Site" "Founder site"
Set-FounderSourceLiteral $legacyUpdater "https://voideditor.com/" "https://doxxedcrypto.digital/"
Set-FounderSourceLiteral $legacyUpdater "Void Error:" "Founder IDE update error:"
Set-FounderSourceLiteral $legacyUpdater "reinstall Void" "reinstall Founder IDE"
Set-FounderSourceLiteral $legacyUpdater "Void Update" "Founder IDE Update"
Set-FounderSourceLiteral $legacyUpdater "Void: Check for Updates" "Founder IDE: Check for Updates"

$legacyUpdateMain = "src\vs\workbench\contrib\void\electron-main\voidUpdateMainService.ts"
Set-FounderSourceLiteral $legacyUpdateMain "Restart Void to update!" "Restart Founder IDE to update!"
Set-FounderSourceLiteral $legacyUpdateMain "A new version of Void is available!" "A new version of Founder IDE is available!"
Set-FounderSourceLiteral $legacyUpdateMain "Void is up-to-date!" "Founder IDE is up-to-date!"

# Void renamed VS Code's auxiliary bar throughout the shell. Founder uses a
# plain product term so layout menus describe the destination instead of the
# upstream fork. Keep this exact list explicit so new surfaces require review.
$assistantPanelSources = @(
    "src\vs\workbench\contrib\quickaccess\browser\viewQuickAccess.ts",
    "src\vs\workbench\browser\workbench.contribution.ts",
    "src\vs\workbench\browser\actions\layoutActions.ts",
    "src\vs\workbench\browser\parts\panel\panelActions.ts",
    "src\vs\workbench\browser\parts\paneCompositeBar.ts",
    "src\vs\workbench\browser\parts\auxiliarybar\auxiliaryBarPart.ts",
    "src\vs\workbench\browser\parts\auxiliarybar\auxiliaryBarActions.ts"
)
foreach ($assistantPanelSource in $assistantPanelSources) {
    Set-FounderSourceLiteral $assistantPanelSource "Void Side Bar" "Assistant Panel"
}

$fileActions = "src\vs\workbench\contrib\files\browser\fileActions.contribution.ts"
Set-FounderSourceLiteral $fileActions "&&Open Void Settings" "&&Open Founder Settings"

$dialogHandler = "src\vs\workbench\electron-sandbox\parts\dialogs\dialogHandler.ts"
Set-FounderSourceLiteral $dialogHandler "VSCode Version: {0}" "Editor Core Version: {0}"
Set-FounderSourceLiteral $dialogHandler "Void Version: {1}" "Founder IDE Version: {1}"

# --- Verify product.json -----------------------------------------------------
$productF = Join-Path $VscodiumCheckout "product.json"
try {
    $product = Get-Content $productF -Raw | ConvertFrom-Json
} catch {
    throw "product.json failed to parse after overlay: $($_.Exception.Message)"
}
$required = @(
    @{ Name = "nameLong";            Want = "Founder IDE" },
    @{ Name = "applicationName";     Want = "founder-ide" }
)
foreach ($r in $required) {
    $got = $product.($r.Name)
    if ($got -ne $r.Want) {
        throw "product.json $($r.Name)='$got' (expected '$($r.Want)') - overlay may be stale or upstream regressed"
    }
}

# win32x64UserAppId is stored double-braced in product.json (Inno Setup escapes
# `{` as `{{` in the ISS template rendering). Verify the GUID body matches.
$appIdRaw = [string]$product.win32x64UserAppId
if ($appIdRaw -notmatch '\{\{436F5001-DD46-4F41-8D93-89EF9716CC07\}\}') {
    throw "product.json win32x64UserAppId='$appIdRaw' (expected '{{436F5001-DD46-4F41-8D93-89EF9716CC07}}') - overlay may be stale or upstream regressed"
}
Write-Host "[apply-founder] product.json OK: nameLong='$($product.nameLong)' applicationName='$($product.applicationName)'" -ForegroundColor Green

# --- Verify the Gateway rewire landed ----------------------------------------
$sendLlm = Join-Path $VscodiumCheckout "src\vs\workbench\contrib\void\electron-main\llmMessage\sendLLMMessage.ts"
$sendFos = Join-Path $VscodiumCheckout "src\vs\workbench\contrib\void\electron-main\llmMessage\sendFounderOs.ts"
$nativeCoordination = Join-Path $VscodiumCheckout "src\vs\workbench\contrib\void\electron-main\llmMessage\founderNativeCoordination.ts"
$personalAiBridge = Join-Path $VscodiumCheckout "src\vs\workbench\contrib\void\browser\founderPersonalAiActions.ts"
if (-not (Test-Path $sendLlm)) { throw "sendLLMMessage.ts missing at $sendLlm" }
if (-not (Test-Path $sendFos)) { throw "sendFounderOs.ts missing at $sendFos" }
if (-not (Test-Path $nativeCoordination)) { throw "founderNativeCoordination.ts missing at $nativeCoordination" }
if (-not (Test-Path $personalAiBridge)) { throw "founderPersonalAiActions.ts missing at $personalAiBridge" }
$sendLlmContent = Get-Content $sendLlm -Raw
if ($sendLlmContent -notmatch "FOUNDER_OS_GATEWAY_REWIRE") {
    throw "sendLLMMessage.ts is missing the FOUNDER_OS_GATEWAY_REWIRE marker - overlay did not apply"
}
if ((Get-Content $chatThreadService -Raw) -notmatch 'workspacePath: this\._workspaceContextService') {
    throw "Native chat is missing workspace-scoped coordination metadata"
}
$personalAiBridgeContent = Get-Content $personalAiBridge -Raw
foreach ($commandId in @("founder.personalAi.save", "founder.personalAi.select", "founder.managedAi.select")) {
    if ($personalAiBridgeContent -notmatch [regex]::Escape($commandId)) {
        throw "Founder Personal AI bridge is missing command '$commandId'"
    }
}
if ($personalAiBridgeContent -notmatch "Remote Personal AI providers must use HTTPS") {
    throw "Founder Personal AI bridge is missing remote URL hardening"
}
$sendFosLen = (Get-Item $sendFos).Length
if ($sendFosLen -lt 5000) {
    throw "sendFounderOs.ts is suspiciously small ($sendFosLen bytes) - overlay may be truncated"
}
Write-Host "[apply-founder] Gateway rewire OK: sendLLMMessage.ts patched, sendFounderOs.ts present ($sendFosLen bytes)" -ForegroundColor Green

# --- Verify clean-profile Founder AI defaults -------------------------------
$modelCapabilities = Join-Path $VscodiumCheckout "src\vs\workbench\contrib\void\common\modelCapabilities.ts"
$settingsService = Join-Path $VscodiumCheckout "src\vs\workbench\contrib\void\common\voidSettingsService.ts"
if (-not (Test-Path $modelCapabilities) -or -not (Test-Path $settingsService)) {
    throw "Founder AI default-model sources are missing after overlay application"
}
$modelCapabilitiesContent = Get-Content $modelCapabilities -Raw
$settingsServiceContent = Get-Content $settingsService -Raw
foreach ($modelAlias in @("founder-os-auto", "founder-os-fast", "founder-os-reasoning", "founder-os-code")) {
    if ($modelCapabilitiesContent -notmatch [regex]::Escape($modelAlias)) {
        throw "modelCapabilities.ts is missing managed model alias '$modelAlias'"
    }
}
if ($settingsServiceContent -notmatch "modelName: 'founder-os-auto'") {
    throw "voidSettingsService.ts does not select founder-os-auto for a clean profile"
}
if ($settingsServiceContent -notmatch "modelName: 'founder-os-code'") {
    throw "voidSettingsService.ts does not select founder-os-code for clean-profile autocomplete"
}
Write-Host "[apply-founder] clean-profile Founder AI defaults OK: Auto chat + Code autocomplete" -ForegroundColor Green

# --- Verify code.iss ---------------------------------------------------------
$codeIss = Join-Path $VscodiumCheckout "build\win32\code.iss"
if (-not (Test-Path $codeIss)) { throw "build/win32/code.iss missing at $codeIss" }
$codeIssContent = Get-Content $codeIss -Raw
if ($codeIssContent -notmatch "OutputBaseFilename=FounderIDESetup") {
    throw "build/win32/code.iss missing 'OutputBaseFilename=FounderIDESetup' - overlay did not apply"
}
if ($codeIssContent -notmatch "AppPublisher=Doxxed Crypto") {
    throw "build/win32/code.iss missing 'AppPublisher=Doxxed Crypto' - overlay did not apply"
}
# The tools\* Source line must be wrapped in #ifexist "tools\*" so the inner
# installer compiles even when the open-source gulp targets omit tools/
# (the remote-tunnel CLI is only produced by VS Code's official Azure pipeline).
# Regression check for the 0.9.2 CI fix.
if ($codeIssContent -notmatch '#ifexist "tools\\\*"') {
    throw "build/win32/code.iss missing #ifexist `"tools\*`" guard on the tools Source line - overlay did not apply (regression of 0.9.2 CI fix)"
}
Write-Host "[apply-founder] code.iss OK: OutputBaseFilename=FounderIDESetup, AppPublisher=Doxxed Crypto, #ifexist tools guard present" -ForegroundColor Green

Write-Host ""
Write-Host "[apply-founder] DONE - Founder IDE customizations applied. Next: npm ci, then gulp vscode-win32-x64." -ForegroundColor Green
