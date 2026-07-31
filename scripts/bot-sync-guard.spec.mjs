import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const guardPath = resolve('scripts/lib/bot-sync-guard.mjs');

function runGuard({ allowBluntSync, forced = false, checkOnly = false }) {
  const root = mkdtempSync(join(tmpdir(), 'bot-sync-guard-'));
  mkdirSync(join(root, 'config'));
  writeFileSync(
    join(root, 'config', 'bot-architecture.lock.json'),
    JSON.stringify({
      allowBluntSync,
      canonicalSource: { directory: 'services/btc-conservative-agent/' },
      forceOverride: { cliFlag: '--force', envVar: 'BOT_SYNC_FORCE' },
    }),
  );
  try {
    const source = [
      `import { assertBotSyncAllowed } from ${JSON.stringify(`file:///${guardPath.replaceAll('\\', '/')}`)};`,
      `assertBotSyncAllowed({ root: process.env.TEST_ROOT, syncKind: 'contract-test', checkOnly: ${checkOnly} });`,
    ].join('\n');
    return spawnSync(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        source,
        ...(forced ? ['--', '--force'] : []),
      ],
      {
        encoding: 'utf8',
        env: {
          ...process.env,
          TEST_ROOT: root,
          BOT_SYNC_FORCE: forced ? '1' : '',
        },
      },
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test('force flag alone cannot replace the canonical bot', () => {
  const result = runGuard({ allowBluntSync: false, forced: true });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /requires BOTH/i);
});

test('reviewed lock alone still requires invocation confirmation', () => {
  const result = runGuard({ allowBluntSync: true, forced: false });
  assert.equal(result.status, 1);
});

test('reviewed lock plus explicit confirmation permits replacement', () => {
  const result = runGuard({ allowBluntSync: true, forced: true });
  assert.equal(result.status, 0, result.stderr);
});

test('read-only checks never require a replacement override', () => {
  const result = runGuard({
    allowBluntSync: false,
    forced: true,
    checkOnly: true,
  });
  assert.equal(result.status, 0, result.stderr);
});
