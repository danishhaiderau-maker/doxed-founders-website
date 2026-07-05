/**
 * Expire stale INTENT participants on terminal (EXPIRED) cycles — safe one-shot cleanup.
 */
import fs from 'fs';
import path from 'path';
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

const TRADE_IDS = ['cont-48d547bf2d12', 'cont-5389f6237fd4'];
const prisma = new PrismaClient();

async function main() {
  const cycles = await prisma.signalCycle.findMany({
    where: { tradeId: { in: TRADE_IDS } },
    include: { participants: true },
  });
  let updated = 0;
  for (const cycle of cycles) {
    const cycleTerminal =
      cycle.status === SignalCycleStatus.EXPIRED || cycle.status === SignalCycleStatus.CLOSED;
    if (!cycleTerminal) {
      console.log(`skip cycle ${cycle.tradeId} status=${cycle.status} (not terminal)`);
      continue;
    }
    for (const p of cycle.participants) {
      if (p.status !== SignalCycleStatus.INTENT) continue;
      await prisma.signalCycleParticipant.update({
        where: { id: p.id },
        data: { status: SignalCycleStatus.EXPIRED },
      });
      await prisma.signalCycleEvent.create({
        data: {
          cycleId: cycle.id,
          participantId: p.id,
          eventType: 'EXPIRED',
          payload: { source: 'admin_cleanup', reason: 'STALE_INTENT_ON_TERMINAL_CYCLE', trade_id: cycle.tradeId },
        },
      });
      updated++;
      console.log(`expired INTENT participant ${p.id.slice(0, 8)} cycle=${cycle.tradeId}`);
    }
  }
  console.log(`done updated=${updated}`);
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
