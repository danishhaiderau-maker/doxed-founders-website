#!/usr/bin/env node
/** Copy local Final Bots lab → global showcase agent dir (bot.py + pathway + analyzer). */
import { readFileSync, writeFileSync, copyFileSync, existsSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const AGENT = join(ROOT, 'services/btc-conservative-agent');
const TARGET = join(AGENT, 'bot.py');
const LOCAL_DIR = process.env.LOCAL_BOT_DIR ?? 'C:/Users/user/Desktop/Final Bots';
const LOCAL = process.env.LOCAL_BOT ?? join(LOCAL_DIR, 'bybit_bot.py');

const LOCAL_MIRROR_FILES = [
  ['combo_pathway_config.py', join(AGENT, 'combo_pathway_config.py')],
  ['legacy_pathway_config.py', join(AGENT, 'legacy_pathway_config.py')],
  ['experimental_pathway_config.py', join(AGENT, 'experimental_pathway_config.py')],
  ['scenario_c_config.py', join(AGENT, 'scenario_c_config.py')],
  ['pathway_lane_roster.py', join(AGENT, 'pathway_lane_roster.py')],
  ['pathway_lab_validation.py', join(AGENT, 'pathway_lab_validation.py')],
  ['analyzer_research_engine_v62.py', join(AGENT, 'research/analyzer_research_engine_v62.py')],
  ['analyzer_research_engine_v62.py', join(AGENT, 'analyzer_research_engine_v62.py')],
];

function sha256(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex').slice(0, 12);
}

function patchForProduction(source) {
  let out = source;
  out = out.replace(
    /^DEEPSEEK_API_KEY\s*=\s*(?!.*getenv)[^\n]+/m,
    'DEEPSEEK_API_KEY = (os.getenv("DEEPSEEK_API_KEY") or "").strip() or None',
  );
  out = out.replace(
    /^BITFINEX_API_KEY\s*=\s*(?!.*getenv)["'][^"']*["']/m,
    'BITFINEX_API_KEY = os.getenv("BITFINEX_API_KEY", "").strip()',
  );
  out = out.replace(
    /^BITFINEX_API_SECRET\s*=\s*(?!.*getenv)["'][^"']*["']/m,
    'BITFINEX_API_SECRET = os.getenv("BITFINEX_API_SECRET", "").strip()',
  );
  out = out.replace(
    /_AGENT_DEBUG_LOG = r"C:\\Users\\user\\Desktop\\Final Bots\\debug-43f630\.log"/,
    '_AGENT_DEBUG_LOG = os.path.join(os.getenv("AGENT_DEBUG_LOG_DIR", "/tmp"), "agent-debug.log")',
  );
  out = out.replace(
    /_AGENT_DEBUG_LOG_ALT = r"C:\\Users\\user\\Desktop\\BOT\\debug-43f630\.log"/,
    '_AGENT_DEBUG_LOG_ALT = os.path.join(os.getenv("AGENT_DEBUG_LOG_DIR", "/tmp"), "agent-debug-alt.log")',
  );
  return out;
}

function extractStackVersion(comboSrc) {
  const m = comboSrc.match(/RESEARCH_STACK_VERSION\s*=\s*"([^"]+)"/);
  return m?.[1] ?? 'unknown';
}

for (const [rel, dest] of LOCAL_MIRROR_FILES) {
  const src = join(LOCAL_DIR, rel);
  if (!existsSync(src)) {
    console.warn(`Skip missing local file: ${src}`);
    continue;
  }
  copyFileSync(src, dest);
  console.log(`Mirrored ${rel} -> ${dest.replace(ROOT + '/', '')}`);
}

const raw = readFileSync(LOCAL, 'utf8');
writeFileSync(TARGET, patchForProduction(raw), 'utf8');
execSync('node scripts/patch-btc-bot-production.mjs', { cwd: ROOT, stdio: 'inherit' });
const final = readFileSync(TARGET, 'utf8');
const comboPath = join(AGENT, 'combo_pathway_config.py');
const stackVer = existsSync(comboPath) ? extractStackVersion(readFileSync(comboPath, 'utf8')) : 'unknown';
console.log(`Synced ${LOCAL} -> ${TARGET} (stack ${stackVer}, hash ${sha256(final)})`);
