#!/usr/bin/env node
/**
 * Pull latest bybit_bot.py from danishhaiderau-maker/bybit-15m-research-bot
 * into services/btc-conservative-agent/bot.py for Railway deployment.
 *
 * Usage: node scripts/sync-btc-research-bot.mjs [--check-only]
 */
import { writeFileSync, readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execSync } from 'node:child_process';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const TARGET = join(ROOT, 'services/btc-conservative-agent/bot.py');
const REPO = process.env.BTC_RESEARCH_REPO ?? 'danishhaiderau-maker/bybit-15m-research-bot';
const SOURCE_FILE = process.env.BTC_RESEARCH_FILE ?? 'bybit_bot.py';
const GITHUB_TOKEN = process.env.GITHUB_TOKEN ?? process.env.GH_TOKEN;

async function fetchResearchBot() {
  const apiUrl = `https://api.github.com/repos/${REPO}/contents/${SOURCE_FILE}`;
  /** @type {Record<string, string>} */
  const headers = {
    Accept: 'application/vnd.github.raw',
    'User-Agent': 'doxedcryptofounder-sync',
  };
  if (GITHUB_TOKEN) headers.Authorization = `Bearer ${GITHUB_TOKEN}`;

  const res = await fetch(apiUrl, { headers });
  if (!res.ok) {
    throw new Error(
      `Failed to fetch ${REPO}/${SOURCE_FILE}: ${res.status}. ` +
        (GITHUB_TOKEN ? '' : 'Set GITHUB_TOKEN for private repos, or run via `gh auth login`.'),
    );
  }
  return res.text();
}

/** Strip hardcoded secrets from research repo — Railway uses Admin Control keys only. */
function stripEmbeddedSecrets(source) {
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
  return out;
}

function patchForProduction(source) {
  let out = stripEmbeddedSecrets(source);
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

function sha256(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex').slice(0, 12);
}

function applyProductionPatches() {
  execSync('node scripts/patch-btc-bot-production.mjs', { cwd: ROOT, stdio: 'inherit' });
}

async function main() {
  const checkOnly = process.argv.includes('--check-only');
  console.log(`Fetching ${REPO}/${SOURCE_FILE}`);
  const raw = await fetchResearchBot();
  if (!raw.includes('Flask') || raw.length < 50_000) {
    throw new Error('Downloaded file does not look like a valid bot.py');
  }

  const oldHash = existsSync(TARGET) ? sha256(readFileSync(TARGET, 'utf8')) : null;

  if (checkOnly) {
    const patched = patchForProduction(raw);
    const probeHash = sha256(patched);
    if (oldHash === probeHash) {
      console.log('Already up to date — no changes.');
      return;
    }
    console.log('Update available (--check-only, not writing).');
    process.exit(2);
  }

  writeFileSync(TARGET, patchForProduction(raw), 'utf8');
  applyProductionPatches();
  const finalSrc = readFileSync(TARGET, 'utf8');
  const newHash = sha256(finalSrc);

  console.log(`Research bot size: ${(finalSrc.length / 1024).toFixed(0)} KB`);
  console.log(`Hash: ${newHash}${oldHash ? ` (was ${oldHash})` : ''}`);

  if (oldHash === newHash) {
    console.log('Already up to date — no changes.');
    return;
  }

  console.log(`Updated ${TARGET}`);
  console.log('Push to master → Railway redeploys btc-conservative-agent automatically.');
  console.log(
    'IMPORTANT: DeepSeek + Bitfinex keys come from Admin Control (/admin/control), NOT from bybit_bot.py.',
  );
  console.log('After deploy, run: npm run push:showcase-bot  (or Push to Runtime in Admin)');

  if (process.argv.includes('--push-credentials')) {
    execSync('node scripts/push-showcase-bot-credentials.mjs', { cwd: ROOT, stdio: 'inherit' });
  }
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
