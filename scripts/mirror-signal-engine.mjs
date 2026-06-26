#!/usr/bin/env node
/**
 * Mirror canonical showcase bot → btc-signal-engine for CI parity checks.
 * Safe direction: services/btc-conservative-agent/* → services/btc-signal-engine/*
 * Does NOT pull from external bybit_bot.py (use sync-btc-research-bot for that).
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const bot = join(root, 'services/btc-conservative-agent/bot.py');
const engine = join(root, 'services/btc-signal-engine/engine.py');
const combosAgent = join(root, 'services/btc-conservative-agent/combo_pathway_config.py');
const combosEngine = join(root, 'services/btc-signal-engine/combos.py');
const manifestPath = join(root, 'services/btc-signal-engine/manifest.json');

function sha256(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex').slice(0, 12);
}

function extractEngineVersion(comboSrc) {
  const direct = comboSrc.match(/EXECUTION_FIX_VERSION\s*=\s*"([^"]+)"/);
  if (direct?.[1]) return direct[1];
  const stack = comboSrc.match(/RESEARCH_STACK_VERSION\s*=\s*"([^"]+)"/);
  return stack?.[1] ?? 'unknown';
}

if (!existsSync(bot)) {
  console.error('Missing canonical bot.py');
  process.exit(1);
}

const botSrc = readFileSync(bot, 'utf8');
const botHash = sha256(botSrc);
writeFileSync(engine, botSrc, 'utf8');
console.log(`Mirrored bot.py → engine.py (${botHash})`);

if (existsSync(combosAgent)) {
  const comboSrc = readFileSync(combosAgent, 'utf8');
  writeFileSync(combosEngine, comboSrc, 'utf8');
  console.log(`Mirrored combo_pathway_config.py → combos.py (${sha256(comboSrc)})`);

  const manifest = {
    engine_version: extractEngineVersion(comboSrc),
    combo_version: new Date().toISOString().slice(0, 10),
    exit_version: 'scenario-c-v4',
    benchmark_lane: 'CONTINUOUS',
    signal_hash: botHash,
    source: 'services/btc-conservative-agent/bot.py',
    updated_at: new Date().toISOString(),
  };
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  console.log(`Updated manifest (engine=${manifest.engine_version} hash=${botHash})`);
}

console.log('Done — run npm run verify:signal-parity to confirm.');
