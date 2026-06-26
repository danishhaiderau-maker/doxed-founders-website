#!/usr/bin/env node
/**
 * Signal parity gate — Phase 1 file hash + Phase 2 combo/signal-flag probe.
 */
import { readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const bot = join(root, 'services/btc-conservative-agent/bot.py');
const engine = join(root, 'services/btc-signal-engine/engine.py');
const manifestPath = join(root, 'services/btc-signal-engine/manifest.json');
const combosAgent = join(root, 'services/btc-conservative-agent/combo_pathway_config.py');
const combosEngine = join(root, 'services/btc-signal-engine/combos.py');
const probe = join(root, 'services/btc-signal-engine/signal_probe.py');
const fixtures = join(root, 'tests/fixtures/signal-parity-cases.json');

function sha256(path) {
  const text = readFileSync(path, 'utf8').replace(/\r\n/g, '\n');
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

if (!existsSync(fixtures)) fail('Missing tests/fixtures/signal-parity-cases.json');
if (!existsSync(probe)) fail('Missing services/btc-signal-engine/signal_probe.py');

console.log('\n--- Phase 2: combo fixture probe ---\n');
try {
  execSync(`python "${probe}"`, { cwd: root, stdio: 'inherit', encoding: 'utf8' });
} catch {
  fail('signal_probe.py failed');
}

const fullProbe = process.argv.includes('--full');
if (fullProbe) {
  console.log('\n--- Phase 2b: signal-flag probe (imports bot.py) ---\n');
  try {
    execSync(`python "${probe}" --full`, { cwd: root, stdio: 'inherit', encoding: 'utf8', timeout: 120_000 });
  } catch {
    fail('signal_probe.py --full failed');
  }
} else {
  console.log('Tip: npm run verify:signal-parity -- --full for bot import flag parity\n');
}

console.log('All parity checks passed.\n');
