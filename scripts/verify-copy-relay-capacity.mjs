#!/usr/bin/env node
/**
 * Verify OPEN + PENDING <= capacity_limit for all Bitfinex copy instances.
 * Usage: node scripts/verify-copy-relay-capacity.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient, SignalCycleStatus } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

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

async function main() {
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) throw new Error('agent missing');

  let violations = 0;
  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
    include: { user: { select: { platformHandle: true, name: true } } },
  });

  console.log('\n=== Copy relay capacity verify ===\n');

  for (const inst of instances) {
    const who = inst.user?.platformHandle ?? inst.user?.name ?? inst.userId;
    const dash = inst.dashboardState ?? {};
    const cap = dash.copyRelayCapacity;
    const open = await prisma.signalCycleParticipant.count({
      where: {
        userId: inst.userId,
        status: SignalCycleStatus.OPEN,
        cycle: { agentId: agent.id },
      },
    });
    const pending = await prisma.signalCycleParticipant.count({
      where: {
        userId: inst.userId,
        status: SignalCycleStatus.PENDING_ENTRY,
        cycle: { agentId: agent.id },
      },
    });
    const limit = cap?.capacityLimit ?? 3;
    const total = open + pending;
    const ok = total <= limit;
    console.log(
      `${ok ? 'OK ' : 'FAIL'} ${who} | OPEN=${open} PENDING=${pending} total=${total} limit=${limit} | instance=${inst.status}`,
    );
    if (cap) {
      console.log(
        `     dashboard: open=${cap.activeOpen} pending=${cap.activePending} showcase=${cap.showcaseMaxActiveSignals}`,
      );
    }
    if (!ok) violations += 1;
  }

  if (violations) {
    console.log(`\n${violations} capacity violation(s)\n`);
    process.exit(1);
  }
  console.log('\nAll instances within capacity.\n');
  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
