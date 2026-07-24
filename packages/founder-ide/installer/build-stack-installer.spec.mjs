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
    assert.match(installerSource, /AfterInstall: WriteFounderReleaseMarker/);
    assert.match(installerSource, /Founder IDE\\founder-release\.json/);
    assert.match(
      installerSource,
      /\{"version":"\{#FOUNDER_STACK_VERSION\}"\}/,
    );
  });
});
