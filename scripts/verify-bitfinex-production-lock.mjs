#!/usr/bin/env node
/**
 * Guard: Bitfinex production showcase + copy policy markers must survive bybit_bot.py sync.
 * Run after patch-btc-bot-production.mjs or before deploy.
 */
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const botPath =
  process.env.BTC_BOT_PATCH_TARGET?.trim() ||
  join(root, 'services/btc-conservative-agent/bot.py');

const BOT_MARKERS = [
  'BITFINEX_WS_SYMBOL',
  'get_effective_max_active_signals',
  'PATHWAY_SPAWN_LANE_POLICY_VERSION',
  'PATHWAY_LIMIT_ORDER_LANES',
  'EXCHANGE_FEE_PROFILE',
  'ADMIN_MANUAL',
  '/api/set_max_active_signals',
  'CREDENTIALS_FROM',
];

const API_MARKERS = [
  { file: 'apps/api/src/trading-agents/signal-subscriber-execution.service.ts', needles: [
    'resolveMaxConcurrentCopySignals',
    'reconcileUnattributedExchangeFills',
    'closeVirtualLot',
    'evaluateSubscriberLotExit',
    'TradingAgentInstanceStatus.PAUSED',
    'BITFINEX_COPY_POLICY_VERSION',
  ]},
  { file: 'apps/api/src/exchanges/bitfinex-api.client.ts', needles: [
    'BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE',
    'nonceLaneFor',
    'submitMarketClose',
  ]},
  { file: 'packages/utils/src/bitfinex-copy-policy.ts', needles: [
    'BITFINEX_COPY_POLICY_VERSION',
    'BITFINEX_COPY_EXIT_RULES',
  ]},
];

function fail(msg) {
  console.error(`FAIL ${msg}`);
  process.exit(1);
}

function checkBot() {
  if (!existsSync(botPath)) fail(`Missing ${botPath}`);
  const src = readFileSync(botPath, 'utf8');
  const missing = BOT_MARKERS.filter((m) => !src.includes(m));
  if (missing.length) {
    fail(`bot.py missing Bitfinex production markers: ${missing.join(', ')}`);
  }
  if (/(?:^|\n)\s*BYBIT_API_KEY\s*=\s*(?!.*getenv)/m.test(src)) {
    fail('bot.py has hardcoded BYBIT_API_KEY assignment — research file not stripped for production');
  }
  console.log(`OK  bot.py Bitfinex markers (${BOT_MARKERS.length})`);
}

function checkApi() {
  for (const group of API_MARKERS) {
    const path = join(root, group.file);
    if (!existsSync(path)) fail(`Missing ${group.file}`);
    const src = readFileSync(path, 'utf8');
    const missing = group.needles.filter((n) => !src.includes(n));
    if (missing.length) {
      fail(`${group.file} missing copy-policy hooks: ${missing.join(', ')}`);
    }
    console.log(`OK  ${group.file}`);
  }
}

console.log('\n=== Bitfinex production lock verify ===\n');
checkBot();
checkApi();
console.log('\nAll Bitfinex production locks present.\n');
console.log(
  'Note: Live money entry/exit is enforced in NestJS (signal-subscriber-execution),',
);
console.log(
  'not in bybit_bot.py. Research sync cannot change copy relay behavior.\n',
);
