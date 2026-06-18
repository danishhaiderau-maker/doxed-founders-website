/**
 * Deep diagnostic: copy eligibility blockers, recent exits, orphan orders context.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

const neonPath = path.join(getVaultDir(), '.env.neon');
if (fs.existsSync(neonPath)) {
  for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    const key = trimmed.slice(0, idx).trim();
    let val = trimmed.slice(idx + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    process.env[key] = val;
  }
}

const prisma = new PrismaClient();

async function main() {
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) throw new Error('agent missing');

  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, status: 'ACTIVE', exchangeProvider: 'bitfinex' },
    include: { user: { select: { platformHandle: true, name: true, id: true } } },
  });

  console.log('\n=== Copy cycle diagnostic ===\n');

  for (const inst of instances) {
    const label = inst.user?.platformHandle ?? inst.user?.name ?? inst.userId;
    console.log(`Hire: ${label} | lastError=${inst.lastError ?? 'none'}`);

    const participants = await prisma.signalCycleParticipant.findMany({
      where: {
        userId: inst.userId,
        status: { in: ['PENDING_ENTRY', 'OPEN', 'CLOSED', 'EXPIRED'] },
        cycle: { agentId: agent.id },
        updatedAt: { gte: new Date(Date.now() - 6 * 60 * 60 * 1000) },
      },
      include: {
        cycle: { select: { tradeId: true, status: true, expiresAt: true, createdAt: true } },
        events: { orderBy: { createdAt: 'asc' } },
      },
      orderBy: { updatedAt: 'desc' },
      take: 15,
    });

    const open = participants.filter((p) => p.status === 'OPEN').length;
    const pending = participants.filter((p) => p.status === 'PENDING_ENTRY').length;
    console.log(`  Ledger: ${open} OPEN, ${pending} PENDING (last 6h)`);

    const managedOrderIds = new Set();
    for (const p of participants.filter((x) => ['OPEN', 'PENDING_ENTRY'].includes(x.status))) {
      for (const e of p.events) {
        const pl = e.payload ?? {};
        if (pl.bitfinexOrderId) managedOrderIds.add(pl.bitfinexOrderId);
        if (pl.stopOrderId) managedOrderIds.add(pl.stopOrderId);
      }
    }
    console.log(`  Managed order IDs: ${[...managedOrderIds].join(', ') || '(none)'}`);

    const intentCycles = await prisma.signalCycle.count({
      where: { agentId: agent.id, status: 'INTENT' },
    });
    console.log(`  Showcase INTENT cycles waiting: ${intentCycles}`);

    console.log('\n  Recent participant lifecycles:');
    for (const p of participants.slice(0, 8)) {
      const types = p.events.map((e) => e.eventType).join(' → ');
      const lastExit = p.events.find((e) => e.eventType === 'EXIT');
      const exitReason = lastExit?.payload?.exit_reason ?? '';
      console.log(
        `    ${p.cycle.tradeId.slice(0, 10)}… ${p.status} | ${types}${exitReason ? ` | exit=${exitReason}` : ''}`,
      );
    }
  }

  const recentExits = await prisma.signalCycleEvent.findMany({
    where: {
      eventType: { in: ['EXIT', 'EXPIRED'] },
      createdAt: { gte: new Date(Date.now() - 8 * 60 * 60 * 1000) },
    },
    orderBy: { createdAt: 'desc' },
    take: 20,
    include: {
      participant: {
        include: {
          user: { select: { platformHandle: true, name: true } },
          cycle: { select: { tradeId: true } },
        },
      },
    },
  });

  console.log('\nRecent EXIT/EXPIRED (8h):');
  for (const e of recentExits) {
    const who = e.participant?.user?.platformHandle ?? e.participant?.user?.name ?? '?';
    const pl = e.payload ?? {};
    console.log(
      `  ${e.createdAt.toISOString()} ${e.eventType} ${who} ${e.participant?.cycle?.tradeId?.slice(0, 10)}… ${pl.exit_reason ?? pl.reason ?? ''} pnl=${pl.pnl_usd ?? 0}`,
    );
  }

  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
