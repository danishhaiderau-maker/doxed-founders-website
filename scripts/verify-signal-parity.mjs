#!/usr/bin/env node
/**
 * Signal parity gate — ensures showcase signal engine matches synced research engine.
 *
 * Phase 1: file identity (bot.py === btc-signal-engine/engine.py, manifest hash)
 * Phase 2: frozen-candle signal comparison (TODO: requires signal_probe.py)
 */
import { readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const bot = join(root, 'services/btc-conservative-agent/bot.py');
const engine = join(root, 'services/btc-signal-engine/engine.py');
const manifestPath = join(root, 'services/btc-signal-engine/manifest.json');
const combosAgent = join(root, 'services/btc-conservative-agent/combo_pathway_config.py');
const combosEngine = join(root, 'services/btc-signal-engine/combos.py');

function sha256(path) {
  const text = readFileSync(path, 'utf8');
  return createHash('sha256').update(text, 'utf8').digest('hex').slice(0, 12);
}

function fail(msg) {
  console.error(`PARITY FAIL: ${msg}`);
  process.exit(1);
}

console.log('\n=== Signal parity verify ===\n');

if (!existsSync(bot)) fail('Missing bot.py');
if (!existsSync(engine)) fail('Missing btc-signal-engine/engine.py — run npm run sync:btc-research-bot');

const botHash = sha256(bot);
const engineHash = sha256(engine);
if (botHash !== engineHash) {
  fail(`bot.py (${botHash}) !== engine.py (${engineHash}) — re-run sync`);
}
console.log(`OK  bot.py === engine.py (${botHash})`);

if (existsSync(combosAgent) && existsSync(combosEngine)) {
  const ca = sha256(combosAgent);
  const ce = sha256(combosEngine);
  if (ca !== ce) fail(`combo_pathway_config (${ca}) !== signal-engine/combos.py (${ce})`);
  console.log(`OK  combo configs match (${ca})`);
}

if (existsSync(manifestPath)) {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  if (manifest.signal_hash && manifest.signal_hash !== botHash) {
    fail(`manifest signal_hash ${manifest.signal_hash} !== bot.py ${botHash}`);
  }
  console.log(`OK  manifest engine=${manifest.engine_version} hash=${manifest.signal_hash || botHash}`);
}

const wrapper = join(root, 'services/btc-conservative-agent/btc_conservative_agent.py');
if (!existsSync(wrapper)) fail('Missing btc_conservative_agent.py entry point');
console.log('OK  btc_conservative_agent.py entry point present');

console.log('\nNote: candle-level signal parity (direction/edge/spread/AI/lane) requires Phase 2 probe.');
console.log('All file-level parity checks passed.\n');
