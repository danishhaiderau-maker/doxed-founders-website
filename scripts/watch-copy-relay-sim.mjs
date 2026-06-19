#!/usr/bin/env node
/**
 * Monitor Bitfinex copy-relay simulation — reconcile alerts, blind PENDING states, ledger drift.
 * Usage: node scripts/watch-copy-relay-sim.mjs [--hours 4] [--interval 60]
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

const hours = Number(process.argv.find((a) => a.startsWith('--hours='))?.split('=')[1] ?? 4);
const intervalSec = Number(
  process.argv.find((a) => a.startsWith('--interval='))?.split('=')[1] ?? 60,
);
const logDir = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'logs');
const logFile = path.join(
  logDir,
  `copy-relay-sim-watch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.log`,
);

const RECONCILE_ALERT_BTC = 0.001;

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
const BOT_URL =
  process.env.TRADING_AGENT_BOT_URL ?? 'https://btc-conservative-agent-production.up.railway.app';
const endAt = Date.now() + hours * 3600_000;

function log(line) {
  const row = `[${new Date().toISOString()}] ${line}`;
  console.log(row);
  fs.mkdirSync(logDir, { recursive: true });
  fs.appendFileSync(logFile, row + '\n');
}

function readSimState(dash) {
  if (!dash || typeof dash !== 'object') return { active: false };
  const raw = /** @type {Record<string, unknown>} */ (dash).copyRelaySim;
  if (!raw || typeof raw !== 'object') return { active: false };
  const s = /** @type {Record<string, unknown>} */ (raw);
  return {
    active: Boolean(s.active),
    startedAt: typeof s.startedAt === 'string' ? s.startedAt : null,
    sessionPnlUsd: typeof s.sessionPnlUsd === 'number' ? s.sessionPnlUsd : 0,
    showcasePnlUsd: typeof s.showcasePnlUsd === 'number' ? s.showcasePnlUsd : null,
    reconcile: s.reconcile ?? null,
  };
}

async function snapshot() {
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) return log('ERROR agent missing');

  let bot = {};
  try {
    const res = await fetch(`${BOT_URL}/api/state`, { signal: AbortSignal.timeout(20_000) });
    if (res.ok) bot = await res.json();
  } catch (e) {
    log(`WARN bot fetch: ${e instanceof Error ? e.message : e}`);
  }

  const showcasePnl = Number(
    bot.stats?.total_pnl_usd ?? bot.stats?.session_pnl_usd ?? bot.session_pnl_usd ?? 0,
  );

  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
    include: { user: { select: { platformHandle: true, name: true } } },
  });

  let simCount = 0;

  for (const inst of instances) {
    const dash = inst.dashboardState ?? {};
    const sim = readSimState(dash);
    if (!sim.active) continue;
    simCount += 1;

    const who = inst.user?.platformHandle ?? inst.user?.name ?? inst.userId;
    const since = sim.startedAt ? new Date(sim.startedAt) : new Date(0);
    const reconcile =
      (dash.copyRelayReconcile ?? sim.reconcile) && typeof dash === 'object'
        ? /** @type {Record<string, unknown>} */ (dash).copyRelayReconcile ?? sim.reconcile
        : sim.reconcile;

    const open = await prisma.signalCycleParticipant.count({
      where: {
        userId: inst.userId,
        status: 'OPEN',
        createdAt: { gte: since },
        cycle: { agentId: agent.id },
      },
    });
    const pending = await prisma.signalCycleParticipant.count({
      where: {
        userId: inst.userId,
        status: 'PENDING_ENTRY',
        createdAt: { gte: since },
        cycle: { agentId: agent.id },
      },
    });

    const exchangeQty =
      reconcile && typeof reconcile === 'object'
        ? Number(/** @type {Record<string, unknown>} */ (reconcile).exchangePositionQty ?? 0)
        : null;
    const ledgerQty =
      reconcile && typeof reconcile === 'object'
        ? Number(/** @type {Record<string, unknown>} */ (reconcile).ledgerOpenQty ?? 0)
        : null;
    const delta =
      reconcile && typeof reconcile === 'object'
        ? Number(/** @type {Record<string, unknown>} */ (reconcile).deltaBtc ?? 0)
        : null;

    const pnlGap = sim.sessionPnlUsd - showcasePnl;

    log(
      `SIM ${who} | instance=${inst.status} | ledger OPEN=${open} PENDING=${pending} | simPnl=$${sim.sessionPnlUsd.toFixed(2)} showcase=$${showcasePnl.toFixed(2)} gap=$${pnlGap.toFixed(2)}`,
    );

    if (exchangeQty != null && ledgerQty != null && delta != null) {
      log(
        `  reconcile exchange=${exchangeQty.toFixed(5)} ledger=${ledgerQty.toFixed(5)} Δ=${delta.toFixed(5)} BTC`,
      );
      if (Math.abs(delta) > RECONCILE_ALERT_BTC) {
        log(`  ALERT RECONCILE DRIFT |Δ|=${Math.abs(delta).toFixed(5)} > ${RECONCILE_ALERT_BTC} BTC`);
      }
    } else {
      log('  WARN reconcile snapshot missing — tick may be delayed');
    }

    if (open === 0 && exchangeQty != null && exchangeQty > RECONCILE_ALERT_BTC) {
      log(`  ALERT exchange position ${exchangeQty.toFixed(5)} BTC but ledger OPEN=0`);
    }

    if (pending > 0) {
      const filledButPending = await prisma.signalCycleParticipant.count({
        where: {
          userId: inst.userId,
          status: 'PENDING_ENTRY',
          createdAt: { gte: since },
          events: { some: { eventType: 'FILLED' } },
        },
      });
      if (filledButPending > 0) {
        log(`  ALERT ${filledButPending} sim PENDING with FILLED events — risk monitor blind`);
      }
    }

    if (inst.lastError?.includes('RECONCILE ALERT')) {
      log(`  ALERT lastError=${inst.lastError}`);
    }

    const recent = await prisma.signalCycleEvent.findMany({
      where: {
        createdAt: { gte: new Date(Date.now() - intervalSec * 1000 - 30_000) },
        participant: { userId: inst.userId, createdAt: { gte: since } },
        eventType: { in: ['FILLED', 'EXIT', 'ORDER_PLACED', 'EXPIRED'] },
      },
      orderBy: { createdAt: 'desc' },
      take: 6,
      include: { participant: { include: { cycle: { select: { tradeId: true } } } } },
    });

    for (const e of recent) {
      const pl =
        e.payload && typeof e.payload === 'object' && e.payload !== null
          ? /** @type {Record<string, unknown>} */ (e.payload)
          : {};
      log(
        `  event ${e.eventType} ${pl.venue ?? ''} ${pl.exit_reason ?? pl.event ?? ''} trade=${e.participant?.cycle?.tradeId?.slice(0, 10) ?? '?'}`,
      );
    }
  }

  if (simCount === 0) {
    log('No active relay sim instances (hire Bitfinex + start sim from Agent Hub)');
  }

  log(`  bot price=${bot.price ?? '?'} paused=${bot.execution_paused ?? '?'}`);
}

async function main() {
  log(`Relay sim watch — ${hours}h, every ${intervalSec}s → ${logFile}`);
  while (Date.now() < endAt) {
    await snapshot();
    const remaining = Math.max(0, Math.round((endAt - Date.now()) / 60_000));
    log(`--- next tick in ${intervalSec}s (${remaining}m left) ---`);
    await new Promise((r) => setTimeout(r, intervalSec * 1000));
  }
  log('Relay sim watch complete');
  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
