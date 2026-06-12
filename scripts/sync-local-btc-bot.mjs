#!/usr/bin/env node
/** One-off: copy local Final Bots/bybit_bot.py into Railway bot.py with production patches. */
import { readFileSync, writeFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const TARGET = join(ROOT, 'services/btc-conservative-agent/bot.py');
const LOCAL = process.env.LOCAL_BOT ?? 'C:/Users/user/Desktop/Final Bots/bybit_bot.py';

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

const raw = readFileSync(LOCAL, 'utf8');
writeFileSync(TARGET, patchForProduction(raw), 'utf8');
execSync('node scripts/patch-btc-bot-production.mjs', { cwd: ROOT, stdio: 'inherit' });
const final = readFileSync(TARGET, 'utf8');
const ver = final.includes('v1.0.8-ws-stability') ? 'v1.0.8-ws-stability' : 'UNKNOWN';
console.log(`Synced ${LOCAL} -> ${TARGET} (${ver})`);
