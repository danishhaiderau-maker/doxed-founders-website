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
  assert.match(script, /FOUNDER_IDE_DEV_CACHE/);
  assert.match(script, /GetTempPath/);
  assert.doesNotMatch(script, /artifacts\\founder-ide-dev-cache/);
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

    assert.equal(status.mode, 'Status');
    assert.equal(status.overlayFiles, 22);
    assert.ok(status.extensionFiles > 30);
    assert.match(status.overlayHash, /^[a-f0-9]{64}$/);
    assert.match(status.extensionHash, /^[a-f0-9]{64}$/);
    assert.match(status.expectedUpstreamCommit, /^[a-f0-9]{40}$/);
    assert.match(status.expectedNode, /^20\./);
  },
);
