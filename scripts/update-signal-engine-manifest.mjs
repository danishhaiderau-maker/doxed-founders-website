import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const bot = readFileSync(join(root, 'services/btc-conservative-agent/bot.py'), 'utf8');
const combo = readFileSync(join(root, 'services/btc-conservative-agent/combo_pathway_config.py'), 'utf8');
const hash = createHash('sha256').update(bot, 'utf8').digest('hex').slice(0, 12);
const version = combo.match(/EXECUTION_FIX_VERSION\s*=\s*"([^"]+)"/)?.[1] ?? 'unknown';
const manifest = {
  engine_version: version,
  combo_version: new Date().toISOString().slice(0, 10),
  exit_version: 'scenario-c-v4',
  benchmark_lane: 'COMBO_65_SP5_CHASE_3PLUS',
  signal_hash: hash,
  source: 'bybit-15m-research-bot/bybit_bot.py',
  updated_at: new Date().toISOString(),
};
writeFileSync(join(root, 'services/btc-signal-engine/manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
console.log('manifest updated', hash, version);
