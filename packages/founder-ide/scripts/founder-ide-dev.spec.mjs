import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..', '..', '..');
const scriptPath = path.join(here, 'founder-ide-dev.ps1');
const gulpfilePath = path.join(repoRoot, 'packages', 'founder-ide', 'build', 'founder-fast-gulpfile.js');
const overlayManifestPath = path.join(
  repoRoot,
  'packages',
  'founder-ide',
  'upstream',
  'overlay',
  'MANIFEST.json',
);

test('fast Founder IDE workflow is non-destructive and preserves warm output', () => {
  const script = readFileSync(scriptPath, 'utf8');
  const gulpfile = readFileSync(gulpfilePath, 'utf8');

  assert.doesNotMatch(script, /\bRemove-Item\b/i);
  assert.doesNotMatch(script, /\brimraf\b/i);
  assert.doesNotMatch(gulpfile, /\brimraf\b/i);
  assert.match(gulpfile, /watchTask\('out', false\)/);
  assert.match(script, /expectedUpstreamCommit/);
  assert.match(script, /Workbench mode requires Node/);
  assert.match(script, /staged or deleted tracked files/);
  assert.match(script, /requires an existing compiled output/);
  assert.match(script, /extensionDevelopmentPath/);
  assert.match(script, /Format-NamedArgument/);
  assert.match(script, /Format-PositionalArgument \$gulpCli/);
  assert.match(script, /Copy-Item -LiteralPath \$fastGulpfile -Destination \$checkoutGulpfile -Force/);
  assert.match(script, /Format-PositionalArgument \$checkoutGulpfile/);
  assert.match(script, /Format-PositionalArgument \$reactBuild/);
  assert.match(script, /Get-RecordedNodeProcess/);
  assert.match(script, /workbenchWatchStartedAt/);
  assert.match(script, /reactWatchStartedAt/);
  assert.match(script, /\$PSCommandPath/);
  assert.doesNotMatch(script, /SetLastWriteTimeUtc/);
  assert.match(script, /if \(\$overlayApplied\)/);
  assert.match(script, /announcedCompileInputs/);
  assert.match(script, /changedCompileInputs/);
  assert.match(script, /overlayContentHash/);
  assert.match(script, /Wait-ForWorkbenchCompile/);
  assert.match(script, /Wait-ForReactCompile/);
  assert.match(script, /Test-WorkbenchOutputsCurrent/);
  assert.match(script, /workbenchCompileReadyAt/);
  assert.match(script, /reactCompileReadyAt/);
  assert.match(script, /did not reach a verified compiler checkpoint/);
  assert.match(script, /FOUNDER_IDE_DEV_CACHE/);
  assert.match(script, /FOUNDER_IDE_TSC/);
  assert.match(script, /-TscPath \/ FOUNDER_IDE_TSC/);
  assert.match(script, /GetTempPath/);
  assert.doesNotMatch(script, /artifacts\\founder-ide-dev-cache/);
  assert.match(script, /function Start-PinnedNodeProcess/);
  assert.match(script, /\$nodeDirectory = Split-Path -Parent \$NodeExecutable/);
  assert.match(script, /\$env:PATH = "\$nodeDirectory;\$previousPath"/);
  assert.match(script, /-NodeExecutable \$nodeExecutable/);
});

test(
  'status mode derives reproducible overlay and extension cache keys',
  { skip: process.platform !== 'win32' },
  () => {
    const cacheRoot = mkdtempSync(path.join(tmpdir(), 'founder-ide-dev-spec-'));
    const stdout = execFileSync(
      'powershell.exe',
      [
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        scriptPath,
        '-Mode',
        'Status',
        '-CacheRoot',
        cacheRoot,
      ],
      {
        cwd: repoRoot,
        encoding: 'utf8',
        timeout: 120_000,
      },
    );
    const status = JSON.parse(stdout);
    const overlayManifest = JSON.parse(readFileSync(overlayManifestPath, 'utf8'));

    assert.equal(status.mode, 'Status');
    // The cache key covers every manifest-owned file plus the manifest itself,
    // overlay applier, fast gulpfile, and this workflow script.
    assert.equal(status.overlayFiles, overlayManifest.files.length + 4);
    assert.ok(status.overlayFiles >= 24);
    assert.ok(status.extensionFiles > 30);
    assert.match(status.overlayHash, /^[a-f0-9]{64}$/);
    assert.match(status.extensionHash, /^[a-f0-9]{64}$/);
    assert.match(status.expectedUpstreamCommit, /^[a-f0-9]{40}$/);
    assert.match(status.expectedNode, /^20\./);
  },
);
