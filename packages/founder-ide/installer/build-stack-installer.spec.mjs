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

  it('persists a deployment mode during silent installation', () => {
    assert.match(
      installerSource,
      /ModePage\.SelectedValueIndex := 2;\s*SelectedDeploymentMode := 'HYBRID';/,
    );
    assert.match(
      installerSource,
      /ModePage\.SelectedValueIndex := 1;\s*SelectedDeploymentMode := 'PUBLIC';/,
    );
  });
});
