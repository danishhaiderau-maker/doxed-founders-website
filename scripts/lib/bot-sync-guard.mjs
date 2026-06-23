#!/usr/bin/env node
/**
 * Guard blunt bot code sync when config/bot-architecture.lock.json disallows it.
 * Agents and CI should hit this and STOP — call out to the user instead of overwriting bot.py.
 */
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const DEFAULT_LOCK = {
  allowBluntSync: false,
  agentInstruction:
    'STOP: blunt bot sync is disabled. Edit services/btc-conservative-agent/ in this repo instead.',
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
 * @returns {{ allowed: boolean, forced?: boolean }}
 */
export function assertBotSyncAllowed({ root, syncKind, checkOnly = false }) {
  const lock = loadBotArchitectureLock(root);
  const forced = isForceEnabled(lock);

  if (lock.allowBluntSync === true) {
    return { allowed: true };
  }

  if (checkOnly && !forced) {
    console.warn(
      `\n⚠  [bot-sync-guard] ${syncKind} --check-only: read-only probe allowed, but blunt sync is DISABLED.\n` +
        `   ${lock.summary ?? lock.agentInstruction ?? DEFAULT_LOCK.agentInstruction}\n`,
    );
    return { allowed: true, checkOnly: true };
  }

  if (forced) {
    console.warn(
      `\n⚠  [bot-sync-guard] FORCE override for ${syncKind}. Proceeding despite architecture lock.\n` +
        `   Human confirmation assumed (${lock.forceOverride?.envVar ?? 'BOT_SYNC_FORCE'} or --force).\n`,
    );
    return { allowed: true, forced: true };
  }

  const lines = [
    '',
    '══════════════════════════════════════════════════════════════════════',
    '  DANGER: blunt bot sync is BLOCKED (config/bot-architecture.lock.json)',
    '══════════════════════════════════════════════════════════════════════',
    '',
    `  Attempted: ${syncKind}`,
    '',
    '  These are NOT the same bot — do not overwrite blindly:',
  ];

  for (const src of lock.deprecatedBluntSyncSources ?? []) {
    const detail = src.path ?? `${src.repo}/${src.file ?? 'bybit_bot.py'}`;
    lines.push(`    • ${src.label ?? src.id}: ${detail}`);
  }

  lines.push(
    '',
    `  Canonical source: ${lock.canonicalSource?.directory ?? 'services/btc-conservative-agent/'}`,
    `  Global showcase:  :${lock.canonicalSource?.runtime?.match(/:(\d+)/)?.[1] ?? '7002'}  ${lock.canonicalSource?.publicUrl ?? ''}`,
    '',
    '  Safe without code sync: sync:production, wire:home-bot, RECOVER-GLOBAL-STACK',
    '',
    '  FOR AI AGENTS: STOP. Tell the user this sync is dangerous and ask before proceeding.',
    `  ${lock.agentInstruction ?? DEFAULT_LOCK.agentInstruction}`,
    '',
    '  Human override only: BOT_SYNC_FORCE=1 … --force',
    '══════════════════════════════════════════════════════════════════════',
    '',
  );

  console.error(lines.join('\n'));
  process.exit(1);
}
