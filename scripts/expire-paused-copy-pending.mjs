/** One-shot: expire pending copy legs for PAUSED hire instances. */
import fs from 'fs';
import path from 'path';
import { PrismaClient, SignalCycleStatus } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

const neonPath = path.join(getVaultDir(), '.env.neon');
if (fs.existsSync(neonPath)) {
  for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    let v = t.slice(i + 1).trim();
    if ((v[0] === '"' && v.at(-1) === '"') || (v[0] === "'" && v.at(-1) === "'")) v = v.slice(1, -1);
    process.env[t.slice(0, i).trim()] = v;
  }
}

const prisma = new PrismaClient();

async function main() {
  const paused = await prisma.tradingAgentInstance.findMany({
    where: { status: 'PAUSED', exchangeProvider: 'bitfinex' },
    include: { user: { select: { platformHandle: true, name: true } } },
  });

  for (const inst of paused) {
    const pending = await prisma.signalCycleParticipant.findMany({
      where: {
        userId: inst.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId: inst.agentId },
      },
    });
    if (!pending.length) continue;

    const label = inst.user?.platformHandle ?? inst.user?.name ?? inst.userId;
    console.log(`Expiring ${pending.length} pending leg(s) for ${label} (PAUSED)`);

    for (const row of pending) {
      await prisma.signalCycleParticipant.update({
        where: { id: row.id },
        data: { status: SignalCycleStatus.EXPIRED },
      });
      await prisma.signalCycleEvent.create({
        data: {
          cycleId: row.cycleId,
          participantId: row.id,
          eventType: 'EXPIRED',
          payload: { exit_reason: 'USER_RELAY_STOP', source: 'hire' },
        },
      });
    }
  }

  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
