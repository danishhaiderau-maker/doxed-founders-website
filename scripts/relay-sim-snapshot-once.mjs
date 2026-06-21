#!/usr/bin/env node
/** One-shot relay sim diagnostic for Agent Hub comparison. */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

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
const prisma = new PrismaClient();

function sessionPnlFromBot(bot) {
  const start = bot.bot_start_time ?? 0;
  const trades = (bot.trades ?? []).filter((t) => {
    if (!t?.ts) return true;
    const ms = Date.parse(t.ts);
    return !Number.isFinite(ms) || start <= 0 || ms / 1000 >= start - 1;
  });
  const net = trades.reduce((s, t) => s + Number(t.net_pnl_usd ?? 0), 0);
  return { net, count: trades.length, balance: Number(bot.account_balance ?? 500) };
}

function formatUserLabel(user) {
  if (!user) return 'unknown';
  const raw = user.platformHandle ?? user.name ?? '';
  if (typeof raw === 'string' && raw.trim()) {
    return raw.replace(/\s*·\s*undefined\s*$/i, '').trim() || raw.trim();
  }
  return user.name?.trim() || 'user';
}

async function main() {
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  const bot = await fetch(`${BOT_URL}/api/state`, { signal: AbortSignal.timeout(20_000) })
    .then((r) => r.json())
    .catch(() => ({}));
  const showcase = sessionPnlFromBot(bot);

  console.log(JSON.stringify({
    at: new Date().toISOString(),
    bot: {
      version: bot.bot_version,
      runtime: bot.runtime_mode,
      balance: bot.account_balance,
      sessionTrades: bot.trade_count_session,
      computedSessionPnl: showcase.net,
      price: bot.price,
      paused: bot.execution_paused,
      pendingOrders: (bot.orders ?? []).length,
      openPositions: (bot.positions ?? []).length,
    },
  }, null, 2));

  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
    include: { user: { select: { platformHandle: true, name: true } } },
  });

  const rows = [];
  for (const inst of instances) {
    const dash = inst.dashboardState ?? {};
    const sim = dash.copyRelaySim ?? {};
    if (!sim.active) continue;
    const since = sim.startedAt ? new Date(sim.startedAt) : new Date(0);
    const rec = dash.copyRelayReconcile ?? sim.reconcile ?? {};
    const parts = await prisma.signalCycleParticipant.groupBy({
      by: ['status'],
      where: { userId: inst.userId, createdAt: { gte: since }, cycle: { agentId: agent.id } },
      _count: true,
    });
    rows.push({
      user: formatUserLabel(inst.user) || inst.userId,
      instanceStatus: inst.status,
      simStartedAt: sim.startedAt,
      simPnlUsd: sim.sessionPnlUsd,
      paperDerivativesUsd: sim.ledger?.derivativesUsd,
      showcaseSessionPnlUsd: showcase.net,
      pnlGap: (sim.sessionPnlUsd ?? 0) - showcase.net,
      reconcile: {
        exchangeQty: rec.exchangePositionQty,
        ledgerQty: rec.ledgerOpenQty,
        deltaBtc: rec.deltaBtc,
        alert: rec.alert,
        openLots: rec.openLots,
        pendingLots: rec.pendingLots,
      },
      participants: Object.fromEntries(parts.map((p) => [p.status, p._count])),
      lastError: inst.lastError,
    });
  }

  console.log(JSON.stringify({ at: new Date().toISOString(), bot: { version: bot.bot_version, balance: bot.account_balance, sessionPnl: showcase.net, trades: showcase.count }, simInstances: rows }, null, 2));
  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
