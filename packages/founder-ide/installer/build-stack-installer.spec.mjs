import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(root, 'build-stack-installer.ps1'), 'utf8');
const installerSource = fs.readFileSync(path.join(root, 'founder-stack.iss'), 'utf8');

describe('Founder IDE one-app installer orchestrator', () => {
  it('resolves the payload beside either direct or nested VS Code source', () => {
    assert.match(source, /Split-Path -Parent \$vscodeSource/);
    assert.match(source, /"VSCode-win32-x64"/);
    assert.doesNotMatch(source, /\$ideRoot = Join-Path \$VscodiumCheckout "VSCode-win32-x64"/);
  });

  it('embeds Founder Node before packaging the inner installer', () => {
    const embed = source.indexOf('& $embedScript -IdeRoot $ideRoot');
    const packageInner = source.indexOf('vscode-win32-x64-user-setup');
    assert.ok(embed >= 0);
    assert.ok(packageInner > embed);
  });

  it('generates and verifies Founder Node icons before Electron packaging', () => {
    const generateIcon = source.indexOf('generate-founder-node-icon.mjs');
    const packageRelay = source.indexOf('npx electron-builder --win --x64 --dir');
    assert.ok(generateIcon >= 0);
    assert.ok(packageRelay > generateIcon);
    assert.match(source, /build\\icon\.png/);
    assert.match(source, /build\\icon\.ico/);
    assert.match(source, /Founder Node PNG icon is missing or unexpectedly small/);
    assert.match(source, /Founder Node ICO icon is missing or unexpectedly small/);
  });

  it('accepts a verified prebuilt Founder Node payload without replacing older output', () => {
    assert.match(source, /\[string\]\$FounderNodePayloadRoot = ""/);
    assert.match(source, /Prebuilt Founder Node payload not found/);
    assert.match(source, /using verified prebuilt Founder relay payload/);
    assert.match(source, /\$relayAsar = Join-Path \$relayRoot "resources\\app\.asar"/);
    assert.match(source, /\$relayIcon = Join-Path \$relayRoot "resources\\icon\.png"/);
    assert.match(source, /Founder relay application archive is missing or unexpectedly small/);
    assert.match(source, /Founder relay packaged icon is missing or unexpectedly small/);
  });

  it('keeps release compression as the default and labels fast QA installers', () => {
    const innerInstallerSource = fs.readFileSync(
      path.join(root, '..', 'upstream', 'overlay', 'build', 'win32', 'code.iss'),
      'utf8',
    );
    assert.match(source, /\[ValidateSet\("Release", "FastQa"\)\]/);
    assert.match(source, /\[string\]\$InstallerProfile = "Release"/);
    assert.match(source, /FastQa requires -IdePayloadRoot/);
    assert.match(
      source,
      /\$installerCompression = if \(\$InstallerProfile -eq "FastQa"\) \{ "zip" \} else \{ "lzma2\/ultra64" \}/,
    );
    assert.match(
      source,
      /\$innerCompression = if \(\$InstallerProfile -eq "FastQa"\) \{ "zip" \} else \{ "lzma" \}/,
    );
    assert.match(source, /\$installerSuffix = if .*"-internal-qa"/);
    assert.match(source, /FounderCompression\s+= \$innerCompression/);
    assert.match(source, /FounderSolidCompression\s+= \$solidCompression/);
    assert.match(source, /\/DFOUNDER_COMPRESSION=\$installerCompression/);
    assert.match(source, /\/DFOUNDER_INSTALLER_SUFFIX=\$installerSuffix/);
    assert.match(installerSource, /#define FOUNDER_COMPRESSION "lzma2\/ultra64"/);
    assert.match(installerSource, /#define FOUNDER_SOLID_COMPRESSION "yes"/);
    assert.match(installerSource, /Compression=\{#FOUNDER_COMPRESSION\}/);
    assert.match(installerSource, /SolidCompression=\{#FOUNDER_SOLID_COMPRESSION\}/);
    assert.match(
      installerSource,
      /OutputBaseFilename=Founder-IDE-Setup-\{#FOUNDER_STACK_VERSION\}\{#FOUNDER_INSTALLER_SUFFIX\}/,
    );
    assert.match(innerInstallerSource, /#define FounderCompression "lzma"/);
    assert.match(innerInstallerSource, /Compression=\{#FounderCompression\}/);
    assert.match(innerInstallerSource, /SolidCompression=\{#FounderSolidCompression\}/);
  });

  it('refreshes the bundled Founder extension during a warm package run', () => {
    assert.match(source, /founder-ide-extension-unpacked/);
    assert.match(source, /ZipFile\]::ExtractToDirectory\(\$vsixDest/);
    assert.match(source, /embedded Founder extension/);
  });

  it('rejects a stale compiled Founder composer', () => {
    assert.match(source, /expectedFounderComposer/);
    assert.match(source, /Founder Second brain/);
    assert.match(source, /founder\.personalAi\.transcribe/);
    assert.match(source, /Founder work mode: Ask/);
    assert.match(source, /Founder work mode: Plan/);
    assert.match(source, /Founder work mode: Build/);
    assert.match(source, /Founder work mode: Debug/);
    assert.match(source, /Founder work mode: Team/);
    assert.match(source, /founder\.coordinated-build/);
    assert.match(source, /Founder skill/);
    assert.match(source, /resources\\app\\out\\main\.js/);
    assert.match(source, /\$founderPayloadText = \$workbenchText \+ \[System\.IO\.File\]::ReadAllText\(\$electronMainBundle\)/);
    assert.match(source, /staleFounderComposer/);
    assert.match(source, /Founder actions/);
    assert.match(source, /Founder IDE payload is stale or incomplete/);
  });

  it('routes personal AI affordances directly to the AI settings tab', () => {
    const settingsPatch = fs.readFileSync(
      path.join(root, '..', 'scripts', 'patch-founder-settings-entry.py'),
      'utf8',
    );
    assert.match(
      settingsPatch,
      /executeCommand\("founderOs\.openSettings","ai"\)/,
    );
    assert.match(settingsPatch, /legacy_redirected/);
    assert.match(settingsPatch, /personal AI settings action still opens the default tab/i);
  });

  it('routes packaged voice through managed speech with a saved-profile fallback', () => {
    const voicePatch = fs.readFileSync(
      path.join(root, '..', 'scripts', 'patch-founder-voice-endpoint.py'),
      'utf8',
    );
    assert.match(source, /patch-founder-voice-endpoint\.py/);
    assert.match(voicePatch, /api\.z\.ai\/api\/paas\/v4\/audio\/transcriptions/);
    assert.match(voicePatch, /open\.bigmodel\.cn\/api\/paas\/v4\/audio\/transcriptions/);
    assert.match(voicePatch, /glmTranscriptionEndpoint\(profile\)/);
    assert.match(voicePatch, /founderOs\.transcribeVoice/);
    assert.match(voicePatch, /founder\.personalAi\.transcribe/);
    assert.match(voicePatch, /managed speech with a saved-profile fallback/);
    assert.match(voicePatch, /replace\(OLD_PROFILE_REQUIREMENT, "", 1\)/);
    assert.match(installerSource, /FOUNDER_WORKBENCH_PATCH/);
    assert.match(installerSource, /founder-workbench\.desktop\.main\.js/);
    assert.match(installerSource, /InstallFounderWorkbenchPatch/);
    assert.match(installerSource, /AfterInstall: FinalizeFounderInstall/);
    assert.match(source, /\$workbenchPatch = Join-Path \$staging/);
    assert.match(source, /FOUNDER_WORKBENCH_PATCH=/);
  });

  it('ships the Founder workbench with its synchronized integrity manifest', () => {
    const integritySync = fs.readFileSync(
      path.join(root, '..', 'scripts', 'sync-founder-integrity.py'),
      'utf8',
    );
    assert.match(integritySync, /hashlib\.sha256/);
    assert.match(integritySync, /base64\.b64encode/);
    assert.match(
      integritySync,
      /vs\/workbench\/workbench\.desktop\.main\.js/,
    );
    assert.match(source, /sync-founder-integrity\.py/);
    assert.match(source, /\$productJsonPatch/);
    assert.match(source, /FOUNDER_PRODUCT_PATCH=/);
    assert.match(installerSource, /FOUNDER_PRODUCT_PATCH/);
    assert.match(installerSource, /InstallFounderProductPatch/);
    assert.match(
      installerSource,
      /Founder IDE integrity manifest installed\./,
    );
  });

  it('applies the compiled Founder navigation after the inner installer', () => {
    assert.match(source, /\$founderHubPatchSource/);
    assert.match(source, /founder-ide-extension\\out\\founder-hub\.js/);
    assert.match(source, /FOUNDER_HUB_PATCH=/);
    assert.match(installerSource, /FOUNDER_HUB_PATCH/);
    assert.match(installerSource, /procedure InstallFounderHubPatch;/);
    assert.match(
      installerSource,
      /extensions\\founder-ide-extension\\out\\founder-hub\.js/,
    );
    assert.match(
      installerSource,
      /Founder IDE navigation correction installed\./,
    );
  });

  it('rejects payloads missing supported Windows startup bindings', () => {
    assert.match(source, /requiredNativeBindings/);
    assert.match(source, /@parcel\\watcher-win32-x64\\watcher\.node/);
    assert.match(source, /node-pty\\build\\Release\\conpty\.node/);
    assert.match(source, /node-pty\\build\\Release\\conpty_console_list\.node/);
    assert.doesNotMatch(source, /node-pty\\build\\Release\\pty\.node/);
    assert.match(source, /Founder IDE native binding is missing/);
  });

  it('executes the packaged policy watcher contract with the pinned Electron runtime', () => {
    assert.match(source, /refreshed Electron-targeted policy watcher/);
    assert.match(source, /ELECTRON_RUN_AS_NODE/);
    assert.match(source, /createWatcher\('FounderIDE',\{\},\(\)=>\{\}\)/);
    assert.match(source, /policy watcher runtime contract failed/);
    assert.match(source, /policy watcher runtime contract verified/);
  });

  it('restores and validates the pinned ripgrep runtime', () => {
    assert.match(source, /node_modules\\@vscode\\ripgrep\\bin\\rg\.exe/);
    assert.match(source, /Founder IDE ripgrep runtime is missing from both payload and source/);
    assert.match(source, /Founder IDE ripgrep runtime is unexpectedly small/);
    assert.match(source, /5075519D24E22733AACDDDD218C7023FC94C49150397E1EDA5C4F6B866C3174E/);
    assert.match(source, /Get-FileHash -LiteralPath \$ripgrepDest -Algorithm SHA256/);
    assert.match(source, /Founder IDE ripgrep runtime checksum mismatch/);
  });

  it('restores the matching node-pty ConPTY runtime before packaging', () => {
    assert.match(source, /node-pty\\third_party\\conpty/);
    assert.match(source, /win10-x64/);
    assert.match(source, /conpty\.dll/);
    assert.match(source, /OpenConsole\.exe/);
    assert.match(source, /Founder IDE ConPTY runtime is unexpectedly small/);
  });

  it('persists a deployment mode during silent installation', () => {
    assert.match(
      installerSource,
      /WizardIsComponentSelected\('private_core'\).*?Result := 'HYBRID'.*?Result := 'PUBLIC'/s,
    );
    assert.match(
      installerSource,
      /ModePage\.SelectedValueIndex := 2;\s*SelectedDeploymentMode := 'HYBRID';/,
    );
    assert.match(
      installerSource,
      /ModePage\.SelectedValueIndex := 1;\s*SelectedDeploymentMode := 'PUBLIC';/,
    );
  });

  it('writes the installed IDE release identity after the inner installer', () => {
    assert.match(installerSource, /AfterInstall: FinalizeFounderInstall/);
    assert.match(
      installerSource,
      /procedure FinalizeFounderInstall;[\s\S]*?InstallFounderWorkbenchPatch;[\s\S]*?InstallFounderProductPatch;[\s\S]*?InstallFounderHubPatch;[\s\S]*?InstallFounderShortcuts;[\s\S]*?WriteFounderReleaseMarker;/,
    );
    assert.match(installerSource, /Founder IDE\\founder-release\.json/);
    assert.match(
      installerSource,
      /\{"version":"\{#FOUNDER_STACK_VERSION\}"\}/,
    );
  });

  it('recreates all Founder IDE shortcuts for the installing user', () => {
    assert.match(installerSource, /procedure InstallFounderShortcuts;/);
    assert.match(
      installerSource,
      /TargetPath := FounderIdeExe\(''\);/,
    );
    assert.match(installerSource, /\{userdesktop\}\\Founder IDE\.lnk/);
    assert.match(installerSource, /\{userprograms\}\\Founder IDE\.lnk/);
    assert.match(
      installerSource,
      /\{userappdata\}\\Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\TaskBar\\Founder IDE\.lnk/,
    );
    assert.match(installerSource, /CreateShellLink\(/);
    assert.doesNotMatch(installerSource, /if not CreateShellLink\(/);
    assert.match(
      installerSource,
      /Founder IDE shortcuts created for the current user\./,
    );
    assert.doesNotMatch(installerSource, /CodexSandboxOffline/);
  });
});
