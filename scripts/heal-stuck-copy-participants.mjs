/**
 * One-shot: expire stuck PENDING_ENTRY when exchange is flat (heals copy relay block).
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient, SignalCycleStatus } from '@prisma/client';
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
  const stuck = await prisma.signalCycleParticipant.findMany({
    where: { status: SignalCycleStatus.PENDING_ENTRY },
    include: { cycle: { include: { agent: true } }, user: { select: { platformHandle: true, name: true } } },
    take: 20,
  });

  console.log(`Stuck PENDING_ENTRY participants: ${stuck.length}`);
  for (const p of stuck) {
    const filled = await prisma.signalCycleEvent.count({
      where: { participantId: p.id, eventType: 'FILLED' },
    });
    const label = p.user?.platformHandle ?? p.user?.name ?? p.userId;
    console.log(
      `  ${label} cycle=${p.cycleId} trade=${p.cycle.tradeId} filled_events=${filled} agent=${p.cycle.agent.slug}`,
    );

    if (filled > 0) {
      const last = await prisma.signalCycleEvent.findFirst({
        where: { participantId: p.id, eventType: 'FILLED' },
        orderBy: { createdAt: 'desc' },
      });
      const fillPrice = last?.payload?.fill_price ?? null;
      await prisma.signalCycleParticipant.update({
        where: { id: p.id },
        data: { status: SignalCycleStatus.OPEN, fillPrice },
      });
      await prisma.signalCycle.update({
        where: { id: p.cycleId },
        data: { status: SignalCycleStatus.OPEN },
      });
      console.log(`    → healed to OPEN fill=${fillPrice}`);
      continue;
    }

    await prisma.signalCycleParticipant.update({
      where: { id: p.id },
      data: { status: SignalCycleStatus.EXPIRED },
    });
    await prisma.signalCycleEvent.create({
      data: {
        cycleId: p.cycleId,
        participantId: p.id,
        eventType: 'EXPIRED',
        payload: { source: 'admin_heal', reason: 'STALE_PENDING_NO_FILL' },
      },
    });
    console.log('    → expired (no fill events)');
  }

  await prisma.tradingAgentInstance.updateMany({
    where: { status: 'ACTIVE', exchangeProvider: 'bitfinex', lastError: { contains: 'Managing open copy' } },
    data: { lastError: null },
  });

  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
