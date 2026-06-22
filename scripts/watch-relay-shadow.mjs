#!/usr/bin/env node
/**
 * 2-way shadow watch: local bot :7800 vs Bitfinex relay sim liveBook + fidelity.
 * Usage: node scripts/watch-relay-shadow.mjs --hours=2 --interval=90
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const hours = Number(process.argv.find((a) => a.startsWith('--hours='))?.split('=')[1] ?? 2);
const intervalSec = Number(
  process.argv.find((a) => a.startsWith('--interval='))?.split('=')[1] ?? 90,
);
const logDir = path.join(root, 'logs');
const logFile = path.join(
  logDir,
  `relay-shadow-watch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.log`,
);

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

const prisma = new PrismaClient();
const BOT_URL = process.env.TRADING_AGENT_BOT_URL?.trim() || resolveHomeBotPublicUrl();
const API_URL =
  process.env.API_URL?.trim() || 'https://doxed-founders-website-production.up.railway.app';
const endAt = Date.now() + hours * 3600_000;
const RECONCILE_ALERT_BTC = 0.001;

function log(line) {
  const row = `[${new Date().toISOString()}] ${line}`;
  console.log(row);
  fs.mkdirSync(logDir, { recursive: true });
  fs.appendFileSync(logFile, row + '\n');
}

function terminalOutcome(s) {
  const o = String(s?.outcome ?? s?.status ?? '').toUpperCase();
  return ['CLOSED', 'EXPIRED', 'CANCELLED', 'REJECTED', 'SKIPPED'].includes(o);
}

async function fetchBot() {
  try {
    const res = await fetch(`${BOT_URL}/api/state`, { signal: AbortSignal.timeout(25_000) });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    return { ok: true, data: await res.json() };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

async function fetchDashboard(slug = 'conservative-btc') {
  try {
    const res = await fetch(`${API_URL}/api/trading-agents/${slug}/dashboard`, {
      signal: AbortSignal.timeout(45_000),
    });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    return { ok: true, data: await res.json() };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

async function fetchBotStatus() {
  try {
    const res = await fetch(`${API_URL}/api/trading-agents/bot/status`, {
      signal: AbortSignal.timeout(20_000,
      ),
    });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    return { ok: true, data: await res.json() };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

async function fetchSimFromDb() {
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) return null;
  const inst = await prisma.tradingAgentInstance.findFirst({
    where: {
      agentId: agent.id,
      exchangeProvider: 'bitfinex',
      dashboardState: { path: ['copyRelaySim', 'active'], equals: true },
    },
    include: { user: { select: { platformHandle: true, name: true } } },
  });
  if (!inst) {
    const any = await prisma.tradingAgentInstance.findFirst({
      where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
      include: { user: { select: { platformHandle: true, name: true } } },
    });
    if (!any) return null;
    return { active: false, user: any.user?.platformHandle ?? any.user?.name };
  }
  const dash = inst.dashboardState ?? {};
  const sim = dash.copyRelaySim ?? {};
  const since = sim.startedAt ? new Date(sim.startedAt) : new Date(0);
  const open = await prisma.signalCycleParticipant.count({
    where: { userId: inst.userId, status: 'OPEN', createdAt: { gte: since }, cycle: { agentId: agent.id } },
  });
  const pending = await prisma.signalCycleParticipant.count({
    where: {
      userId: inst.userId,
      status: 'PENDING_ENTRY',
      createdAt: { gte: since },
      cycle: { agentId: agent.id },
    },
  });
  const expired = await prisma.signalCycleParticipant.count({
    where: {
      userId: inst.userId,
      status: 'EXPIRED',
      createdAt: { gte: since },
      cycle: { agentId: agent.id },
    },
  });
  return {
    active: true,
    user: inst.user?.platformHandle ?? inst.user?.name,
    sessionPnlUsd: sim.sessionPnlUsd,
    reconcile: dash.copyRelayReconcile ?? sim.reconcile ?? null,
    participants: { OPEN: open, PENDING_ENTRY: pending, EXPIRED: expired },
    lastError: inst.lastError,
  };
}

function summarizeBot(bot) {
  const orders = (bot.orders ?? []).filter((o) => String(o.status ?? '').toUpperCase() === 'PENDING');
  const positions = bot.positions ?? [];
  const signals = (bot.signal_info?.signals ?? []).filter((s) => !terminalOutcome(s));
  const expired = (bot.signal_info?.signals ?? []).filter((s) =>
    String(s.outcome ?? s.status ?? '').toUpperCase().includes('EXPIRE'),
  );
  const trades = bot.trades ?? [];
  const sessionPnl = trades.reduce((s, t) => s + Number(t.net_pnl_usd ?? 0), 0);
  return {
    pending: orders.length,
    positions: positions.length,
    activeSignals: signals.length,
    expiredSignals: expired.length,
    sessionPnl,
    tradeIds: {
      pending: orders.map((o) => o.trade_id ?? o.id).filter(Boolean),
      positions: positions.map((p) => p.trade_id).filter(Boolean),
      signals: signals.map((s) => s.trade_id).filter(Boolean),
    },
  };
}

function summarizeSimLiveBook(lb) {
  if (!lb) return null;
  return {
    pending: (lb.pendingOrders ?? []).length,
    positions: (lb.positions ?? []).length,
    activeSignals: (lb.activeSignals ?? []).length,
    expired: (lb.expiredOrders ?? lb.expired ?? []).length,
    trades: (lb.trades ?? []).length,
  };
}

async function tick() {
  const [botRes, dashRes, statusRes, simDb] = await Promise.all([
    fetchBot(),
    fetchDashboard(),
    fetchBotStatus(),
    fetchSimFromDb(),
  ]);
  const issues = [];

  if (!botRes.ok) {
    log(`ALERT bot offline: ${botRes.error}`);
    return issues;
  }

  const bot = botRes.data;
  const botSum = summarizeBot(bot);
  log(
    `BOT pending=${botSum.pending} pos=${botSum.positions} activeSig=${botSum.activeSignals} expired=${botSum.expiredSignals} sessionPnl=$${botSum.sessionPnl.toFixed(2)} price=${bot.price ?? '?'}`,
  );

  if (!statusRes.ok) {
    log(`ALERT API bot/status: ${statusRes.error}`);
    issues.push('api_offline');
  } else {
    const st = statusRes.data;
    const connected = st.connected ?? st.publicStatus === 'online';
    log(`API bot/status connected=${connected} public=${st.publicStatus ?? '?'}`);
    if (!connected) {
      issues.push('site_mirror_offline');
      log('ALERT site mirror reports bot offline');
    }
  }

  if (!dashRes.ok) {
    log(`ALERT API dashboard: ${dashRes.error}`);
    issues.push('dashboard_offline');
  } else {
    const pub = dashRes.data;
    const showcaseLb = pub.dashboard?.liveBook;
    const showcaseSum = summarizeSimLiveBook(showcaseLb);
    if (showcaseSum) {
      log(
        `SHOWCASE liveBook pending=${showcaseSum.pending} pos=${showcaseSum.positions} activeSig=${showcaseSum.activeSignals} expired=${showcaseSum.expired}`,
      );
      if (showcaseSum.pending !== botSum.pending) {
        issues.push('showcase_pending_mismatch');
        log(
          `ALERT showcase pending ${showcaseSum.pending} != bot ${botSum.pending} | bot=${botSum.tradeIds.pending.join(',')}`,
        );
      }
      if (showcaseSum.positions !== botSum.positions) {
        issues.push('showcase_positions_mismatch');
        log(`ALERT showcase positions ${showcaseSum.positions} != bot ${botSum.positions}`);
      }
    }
  }

  if (simDb?.active) {
    log(
      `RELAY-SIM DB user=${simDb.user} OPEN=${simDb.participants.OPEN} PENDING=${simDb.participants.PENDING_ENTRY} EXPIRED=${simDb.participants.EXPIRED} simPnl=$${Number(simDb.sessionPnlUsd ?? 0).toFixed(2)}`,
    );
    if (simDb.participants.PENDING_ENTRY !== botSum.pending) {
      issues.push('sim_pending_mismatch');
      log(`ALERT sim PENDING_ENTRY ${simDb.participants.PENDING_ENTRY} != bot pending ${botSum.pending}`);
    }
    if (simDb.participants.OPEN !== botSum.positions) {
      issues.push('sim_positions_mismatch');
      log(`ALERT sim OPEN ${simDb.participants.OPEN} != bot positions ${botSum.positions}`);
    }
    const rec = simDb.reconcile;
    if (rec) {
      const delta = Number(rec.deltaBtc ?? 0);
      log(
        `  reconcile exchange=${Number(rec.exchangePositionQty ?? 0).toFixed(5)} ledger=${Number(rec.ledgerOpenQty ?? 0).toFixed(5)} Δ=${delta.toFixed(5)} openLots=${rec.openLots ?? '?'} pendingLots=${rec.pendingLots ?? '?'}`,
      );
      if (Math.abs(delta) > RECONCILE_ALERT_BTC) {
        issues.push('reconcile_drift');
        log(`ALERT reconcile drift |Δ|=${Math.abs(delta).toFixed(5)} BTC`);
      }
      if (botSum.positions === 0 && Number(rec.exchangePositionQty ?? 0) > RECONCILE_ALERT_BTC) {
        issues.push('sim_position_bot_flat');
        log(`ALERT sim holds ${rec.exchangePositionQty} BTC but bot has 0 positions`);
      }
      if (botSum.pending > 0 && Number(rec.pendingLots ?? 0) === 0) {
        issues.push('sim_missing_pending');
        log(`ALERT bot has ${botSum.pending} pending but sim pendingLots=0`);
      }
    }
    const pnlGap = Number(simDb.sessionPnlUsd ?? 0) - botSum.sessionPnl;
    if (Math.abs(pnlGap) > 5) {
      log(`WARN pnl gap sim-showcase $${pnlGap.toFixed(2)}`);
    }
    if (simDb.lastError?.includes('RECONCILE')) {
      issues.push('sim_last_error');
      log(`ALERT sim lastError=${simDb.lastError}`);
    }
  } else if (simDb && !simDb.active) {
    log(`Relay sim OFF (${simDb.user ?? 'no bitfinex hire'})`);
  } else {
    log('No Bitfinex instance for relay sim compare');
  }

  // Bridge + tunnel
  try {
    const bridge = await fetch('http://127.0.0.1:7810/health', { signal: AbortSignal.timeout(3000) });
    if (!bridge.ok) log('WARN local bridge :7810 not OK');
  } catch {
    log('WARN local bridge :7810 unreachable');
  }

  return issues;
}

async function main() {
  log(`Relay shadow watch — ${hours}h every ${intervalSec}s → ${logFile}`);
  log(`BOT_URL=${BOT_URL} API=${API_URL}`);
  let issueCount = 0;
  while (Date.now() < endAt) {
    const issues = await tick();
    issueCount += issues.length;
    const remaining = Math.max(0, Math.round((endAt - Date.now()) / 60_000));
    log(`--- tick done (${issues.length} issues) | ${remaining}m left | cumulative=${issueCount} ---`);
    await new Promise((r) => setTimeout(r, intervalSec * 1000));
  }
  log(`Relay shadow watch complete — ${issueCount} total issue flags`);
  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
