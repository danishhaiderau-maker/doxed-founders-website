#!/usr/bin/env node
/**
 * Loop monitor for live copy trade cycles (default 2 hours).
 * Usage: node scripts/watch-copy-cycle-loop.mjs [--hours 2] [--interval 120]
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

const hours = Number(process.argv.find((a) => a.startsWith('--hours='))?.split('=')[1] ?? 2);
const intervalSec = Number(
  process.argv.find((a) => a.startsWith('--interval='))?.split('=')[1] ?? 120,
);
const logDir = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'logs');
const logFile = path.join(
  logDir,
  `copy-cycle-watch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.log`,
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
const endAt = Date.now() + hours * 3600_000;

function log(line) {
  const row = `[${new Date().toISOString()}] ${line}`;
  console.log(row);
  fs.mkdirSync(logDir, { recursive: true });
  fs.appendFileSync(logFile, row + '\n');
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

  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
    include: { user: { select: { platformHandle: true, name: true } } },
  });

  for (const inst of instances) {
    const who = inst.user?.platformHandle ?? inst.user?.name ?? inst.userId;
    const open = await prisma.signalCycleParticipant.count({
      where: {
        userId: inst.userId,
        status: 'OPEN',
        cycle: { agentId: agent.id },
      },
    });
    const pending = await prisma.signalCycleParticipant.count({
      where: {
        userId: inst.userId,
        status: 'PENDING_ENTRY',
        cycle: { agentId: agent.id },
      },
    });

    const recent = await prisma.signalCycleEvent.findMany({
      where: {
        createdAt: { gte: new Date(Date.now() - intervalSec * 1000 - 30_000) },
        participant: { userId: inst.userId },
        eventType: { in: ['FILLED', 'EXIT', 'STOP_LOSS_ARMED', 'UPDATE_STOPS', 'EXPIRED'] },
      },
      orderBy: { createdAt: 'desc' },
      take: 8,
      include: { participant: { include: { cycle: { select: { tradeId: true } } } } },
    });

    log(
      `${who} | instance=${inst.status} | ledger OPEN=${open} PENDING=${pending} | lastError=${inst.lastError ?? 'none'}`,
    );
    log(
      `  bot max_active=${bot.max_active_signals ?? '?'} paused=${bot.execution_paused ?? '?'} price=${bot.price ?? '?'}`,
    );

    if (inst.status === 'ACTIVE' && open === 0 && pending > 0) {
      const filledButPending = await prisma.signalCycleParticipant.count({
        where: {
          userId: inst.userId,
          status: 'PENDING_ENTRY',
          events: { some: { eventType: 'FILLED' } },
        },
      });
      if (filledButPending > 0) {
        log(`  ALERT ${filledButPending} PENDING with FILLED events — risk monitor may be blind`);
      }
    }

    if (open > 0) {
      log(`  OK risk engaged: ${open} OPEN lot(s) under Scenario C monitoring`);
    }

    for (const e of recent) {
      const pl =
        e.payload && typeof e.payload === 'object' && e.payload !== null
          ? /** @type {Record<string, unknown>} */ (e.payload)
          : {};
      log(
        `  event ${e.eventType} ${pl.event ?? pl.exit_reason ?? ''} trade=${e.participant?.cycle?.tradeId?.slice(0, 10) ?? '?'}`,
      );
    }
  }
}

async function main() {
  log(`Watch started — ${hours}h, every ${intervalSec}s → ${logFile}`);
  while (Date.now() < endAt) {
    await snapshot();
    const remaining = Math.max(0, Math.round((endAt - Date.now()) / 60_000));
    log(`--- next tick in ${intervalSec}s (${remaining}m left) ---`);
    await new Promise((r) => setTimeout(r, intervalSec * 1000));
  }
  log('Watch complete');
  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
