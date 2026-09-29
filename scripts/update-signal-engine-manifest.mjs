import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const bot = readFileSync(join(root, 'services/btc-conservative-agent/bot.py'), 'utf8');
const combo = readFileSync(join(root, 'services/btc-conservative-agent/combo_pathway_config.py'), 'utf8');
// Match verify-signal-parity.mjs exactly on Windows: Git may expose CRLF in the
// worktree while CI checks normalized LF bytes.
const normalizedBot = bot.replace(/\r\n/g, '\n');
const hash = createHash('sha256').update(normalizedBot, 'utf8').digest('hex').slice(0, 12);
const version =
  combo.match(/RESEARCH_STACK_VERSION\s*=\s*"([^"]+)"/)?.[1]
  ?? combo.match(/EXECUTION_FIX_VERSION\s*=\s*"([^"]+)"/)?.[1]
  ?? 'unknown';
const manifestPath = join(root, 'services/btc-signal-engine/manifest.json');
const existing = JSON.parse(readFileSync(manifestPath, 'utf8'));
const manifest = {
  ...existing,
  engine_version: version,
  combo_version: new Date().toISOString().slice(0, 10),
  signal_hash: hash,
  source: 'services/btc-conservative-agent/bot.py',
  updated_at: new Date().toISOString(),
};
writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
console.log('manifest updated', hash, version);
