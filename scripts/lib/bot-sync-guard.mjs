#!/usr/bin/env node
/**
 * Guard any blunt replacement of the canonical Conservative BTC source.
 *
 * A command-line flag is deliberately insufficient. A write requires:
 *   1. a reviewed, source-controlled allowBluntSync=true change; and
 *   2. an explicit BOT_SYNC_FORCE/--force confirmation for that invocation.
 */
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const DEFAULT_LOCK = {
  allowBluntSync: false,
  agentInstruction:
    'STOP: blunt bot sync is disabled. Edit services/btc-conservative-agent/ directly.',
};

export function loadBotArchitectureLock(root) {
  const lockPath = join(root, 'config/bot-architecture.lock.json');
  if (!existsSync(lockPath)) return { ...DEFAULT_LOCK, lockPath: null };
  try {
    return { ...JSON.parse(readFileSync(lockPath, 'utf8')), lockPath };
  } catch {
    return { ...DEFAULT_LOCK, lockPath, parseError: true };
  }
}

function isForceEnabled(lock, argv = process.argv) {
  const flag = lock.forceOverride?.cliFlag ?? '--force';
  const envVar = lock.forceOverride?.envVar ?? 'BOT_SYNC_FORCE';
  const envVal = process.env[envVar];
  return argv.includes(flag) || envVal === '1' || envVal === 'true';
}

/**
 * @param {{ root: string, syncKind: string, checkOnly?: boolean }} opts
 * @returns {{ allowed: boolean, forced?: boolean, reviewedLock?: boolean, checkOnly?: boolean }}
 */
export function assertBotSyncAllowed({ root, syncKind, checkOnly = false }) {
  const lock = loadBotArchitectureLock(root);
  const forced = isForceEnabled(lock);

  if (checkOnly) {
    console.warn(
      `\n[bot-sync-guard] ${syncKind}: read-only probe allowed; source replacement remains disabled.\n` +
        `  ${lock.summary ?? lock.agentInstruction ?? DEFAULT_LOCK.agentInstruction}\n`,
    );
    return { allowed: true, checkOnly: true };
  }

  if (lock.allowBluntSync === true && forced) {
    console.warn(
      `\n[bot-sync-guard] REVIEWED replacement enabled for ${syncKind}.\n` +
        '  Source-controlled allowBluntSync=true and invocation confirmation are both present.\n',
    );
    return { allowed: true, forced: true, reviewedLock: true };
  }

  const lines = [
    '',
    '======================================================================',
    'DANGER: blunt bot source replacement is BLOCKED',
    '======================================================================',
    '',
    `Attempted: ${syncKind}`,
    '',
    'These sources are not interchangeable with the canonical production bot:',
  ];

  for (const source of lock.deprecatedBluntSyncSources ?? []) {
    const detail =
      source.path ?? `${source.repo}/${source.file ?? 'bybit_bot.py'}`;
    lines.push(`  - ${source.label ?? source.id}: ${detail}`);
  }

  lines.push(
    '',
    `Canonical source: ${lock.canonicalSource?.directory ?? 'services/btc-conservative-agent/'}`,
    `Canonical runtime: ${lock.canonicalSource?.runtime ?? 'Fly.io'}`,
    '',
    'Safe operations do not replace bot.py: deployment, mirror startup, data sync, and analysis.',
    '',
    'FOR AI AGENTS: STOP. Edit the canonical in-repo bot directly.',
    `${lock.agentInstruction ?? DEFAULT_LOCK.agentInstruction}`,
    '',
    'Replacement requires BOTH a reviewed allowBluntSync=true commit and BOT_SYNC_FORCE/--force.',
    '======================================================================',
    '',
  );

  console.error(lines.join('\n'));
  process.exit(1);
}
