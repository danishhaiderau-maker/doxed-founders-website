/**
 * Deep diagnose: open lots, events, risk exits, instance errors.
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

function num(v) {
  if (v == null) return null;
  return typeof v === 'object' && typeof v.toNumber === 'function' ? v.toNumber() : Number(v);
}

async function main() {
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) throw new Error('agent missing');

  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, status: 'ACTIVE', exchangeProvider: { not: 'paper' } },
    include: { user: { select: { platformHandle: true, name: true } } },
  });

  console.log('\n=== Hire instances ===');
  for (const inst of instances) {
    const who = inst.user?.platformHandle ?? inst.user?.name ?? inst.userId;
    console.log(`  ${who} | ${inst.exchangeProvider} | lastError=${inst.lastError ?? '(none)'}`);
  }

  const participants = await prisma.signalCycleParticipant.findMany({
    where: {
      cycle: { agentId: agent.id },
      status: { in: ['OPEN', 'PENDING_ENTRY'] },
    },
    include: {
      cycle: true,
      user: { select: { platformHandle: true, name: true } },
      events: { orderBy: { createdAt: 'desc' }, take: 12 },
    },
    orderBy: { updatedAt: 'desc' },
  });

  console.log(`\n=== Active participants (${participants.length}) ===`);
  for (const row of participants) {
    const who = row.user?.platformHandle ?? row.user?.name ?? row.userId;
    console.log(`\n${who} | ${row.status} | cycle=${row.cycleId}`);
    console.log(`  fill=${num(row.fillPrice)} pnl=${num(row.pnlUsd)} trade=${row.cycle.tradeId.slice(0, 12)}…`);
    for (const e of [...row.events].reverse()) {
      const p = e.payload && typeof e.payload === 'object' ? e.payload : {};
      const extra = [
        p.event,
        p.exit_reason,
        p.fill_price != null ? `fill=${p.fill_price}` : null,
        p.stop_price != null ? `stop=${p.stop_price}` : null,
        p.peak_margin_pct != null ? `peak=${p.peak_margin_pct}%` : null,
        p.unreal_margin_pct != null ? `unreal=${p.unreal_margin_pct}%` : null,
        p.qty != null ? `qty=${p.qty}` : null,
      ]
        .filter(Boolean)
        .join(' ');
      console.log(`  ${e.createdAt.toISOString()} ${e.eventType} ${extra}`);
    }
  }

  const recentExits = await prisma.signalCycleParticipant.findMany({
    where: {
      cycle: { agentId: agent.id },
      status: 'CLOSED',
      updatedAt: { gt: new Date(Date.now() - 2 * 3600_000) },
    },
    include: { user: { select: { platformHandle: true } }, events: { where: { eventType: 'EXIT' } } },
    orderBy: { updatedAt: 'desc' },
    take: 15,
  });

  console.log('\n=== Recent exits (2h) ===');
  for (const r of recentExits) {
    const exit = r.events[0]?.payload ?? {};
    console.log(
      `  ${r.user?.platformHandle ?? '?'} pnl=$${num(r.pnlUsd)?.toFixed(2)} reason=${exit.exit_reason ?? '?'} peak=${exit.peak_margin_pct ?? '?'}% unreal=${exit.unreal_margin_pct ?? '?'}%`,
    );
  }

  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
