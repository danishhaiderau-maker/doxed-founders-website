#!/usr/bin/env node
/**
 * Readiness check before simultaneous showcase signal + Bitfinex copy/relay test.
 *
 * Paper relay (safe): hire Bitfinex → Start relay sim on Agent Hub
 * Live money: hire Bitfinex → ensure relay sim OFF → instance ACTIVE
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const neonPath = path.join(getVaultDir(), '.env.neon');

if (fs.existsSync(neonPath)) {
  for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t[0] === '#') continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    let v = t.slice(i + 1).trim();
    if ((v[0] === '"' && v.at(-1) === '"') || (v[0] === "'" && v.at(-1) === "'")) v = v.slice(1, -1);
    process.env[t.slice(0, i).trim()] = v;
  }
}

const BOT_URL = process.env.TRADING_AGENT_BOT_URL?.trim() || resolveHomeBotPublicUrl();
const API_URL =
  process.env.API_URL ?? 'https://doxed-founders-website-production.up.railway.app';

const prisma = new PrismaClient();

function ok(label, detail = '') {
  console.log(`  ✓ ${label}${detail ? ` — ${detail}` : ''}`);
  return true;
}
function fail(label, detail = '') {
  console.log(`  ✗ ${label}${detail ? ` — ${detail}` : ''}`);
  return false;
}
function warn(label, detail = '') {
  console.log(`  ! ${label}${detail ? ` — ${detail}` : ''}`);
}

async function main() {
  console.log('\n=== Bitfinex relay test readiness ===\n');
  let ready = true;

  // Bot
  let bot = {};
  try {
    const res = await fetch(`${BOT_URL}/api/state`, { signal: AbortSignal.timeout(20_000) });
    if (!res.ok) {
      ready = fail('Showcase bot', `HTTP ${res.status}`);
    } else {
      bot = await res.json();
      ok('Showcase bot', `${bot.bot_version} · ${bot.runtime_mode} · $${bot.account_balance}`);
      if (bot.execution_paused) {
        warn('Showcase paused', bot.execution_reason || 'resume on dashboard');
      }
      if (!bot.last_approve_outcome?.trade_id) {
        warn('No recent approve', 'wait for next AI signal cycle');
      } else {
        ok('Last approve', `${bot.last_approve_outcome.status} ${bot.last_approve_outcome.trade_id}`);
      }
      if (!bot.trades_map || typeof bot.trades_map !== 'object') {
        warn('trades_map missing', 'deploy latest showcase bot for closure sync');
      } else {
        ok('Relay trades_map', `${Object.keys(bot.trades_map).length} entries`);
      }
    }
  } catch (e) {
    ready = fail('Showcase bot', e instanceof Error ? e.message : String(e));
  }

  // API
  try {
    const health = await fetch(`${API_URL}/api/health`, { signal: AbortSignal.timeout(15_000) });
    if (health.ok) ok('Production API', API_URL);
    else ready = fail('Production API', `HTTP ${health.status}`);
  } catch (e) {
    ready = fail('Production API', e instanceof Error ? e.message : String(e));
  }

  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) {
    ready = fail('Agent record', 'conservative-btc missing in Neon');
  } else {
    ok('Agent record', agent.slug);
  }

  const instances = agent
    ? await prisma.tradingAgentInstance.findMany({
        where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
        include: { user: { select: { platformHandle: true, name: true } } },
      })
    : [];

  if (!instances.length) {
    warn('Bitfinex hire', 'none — sign in at doxxedcrypto.digital/agent-hub/conservative-btc and hire with API keys');
  } else {
    for (const inst of instances) {
      const dash = inst.dashboardState ?? {};
      const sim = dash.copyRelaySim ?? {};
      const label = inst.user?.platformHandle || inst.user?.name || inst.userId.slice(0, 8);
      ok(`Instance ${label}`, `status=${inst.status} sim=${sim.active ? 'ON' : 'off'}`);
      if (sim.active) {
        warn('Relay sim active', 'live Bitfinex orders blocked — stop sim for real money test');
      } else if (inst.status !== 'ACTIVE') {
        warn('Instance not ACTIVE', inst.lastError || 'resume live copy on Agent Hub');
      }
    }
  }

  const recentCycles = agent
    ? await prisma.signalCycle.findMany({
        where: { agentId: agent.id },
        orderBy: { createdAt: 'desc' },
        take: 3,
      })
    : [];
  if (recentCycles.length) {
    ok('Recent signal cycles', recentCycles.map((c) => `${c.status}:${c.tradeId}`).join(', '));
  } else {
    warn('Signal cycles', 'none yet — API polls showcase on approve (~250ms)');
  }

  console.log('\n--- How to test simultaneous signal + Bitfinex ---\n');
  console.log('1. Paper (recommended first): Hire Bitfinex → Agent Hub → "Start relay sim"');
  console.log('2. Monitor: npm run watch:copy-relay-sim');
  console.log('3. Snapshot: npm run snapshot:relay-sim');
  console.log('4. Live money: Stop relay sim → resume ACTIVE → small margin cap ($20/lot default)');
  console.log('5. Showcase desk: ' + BOT_URL);
  console.log('6. Agent Hub: https://doxxedcrypto.digital/agent-hub/conservative-btc\n');

  console.log(ready ? 'Infrastructure: READY for relay soak (hire + start sim if not done).\n' : 'Infrastructure: FIX blockers above first.\n');
  await prisma.$disconnect();
  process.exit(ready ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
