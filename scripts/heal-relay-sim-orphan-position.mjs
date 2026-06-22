#!/usr/bin/env node
/** One-shot: clear orphan sim paper position when DB ledger is flat but exchange qty > 0. */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
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

  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
    include: { user: { select: { platformHandle: true, name: true } } },
  });

  for (const inst of instances) {
    const dash = inst.dashboardState ?? {};
    const sim = dash.copyRelaySim ?? {};
    if (!sim.active) continue;

    const ledger = sim.ledger ?? {};
    const pos = ledger.position;
    const exchangeQty = pos?.amount ? Math.abs(Number(pos.amount)) : 0;
    const since = sim.startedAt ? new Date(sim.startedAt) : new Date(0);
    const open = await prisma.signalCycleParticipant.count({
      where: {
        userId: inst.userId,
        status: 'OPEN',
        createdAt: { gte: since },
        cycle: { agentId: agent.id },
      },
    });

    if (exchangeQty < 0.00004 || open > 0) {
      console.log(
        `${inst.user?.platformHandle ?? inst.userId}: skip (exchange=${exchangeQty.toFixed(5)} open=${open})`,
      );
      continue;
    }

    const nextLedger = {
      ...ledger,
      position: null,
      orders: (ledger.orders ?? []).filter((o) => o.orderType !== 'STOP'),
    };
    const nextSim = {
      ...sim,
      ledger: nextLedger,
      reconcile: {
        exchangePositionQty: 0,
        ledgerOpenQty: 0,
        deltaBtc: 0,
        alert: false,
        openLots: 0,
        pendingLots: 0,
        markPrice: dash.copyRelayReconcile?.markPrice ?? null,
        updatedAt: new Date().toISOString(),
      },
    };

    await prisma.tradingAgentInstance.update({
      where: { id: inst.id },
      data: {
        lastError: null,
        dashboardState: {
          ...dash,
          copyRelaySim: nextSim,
          copyRelayReconcile: nextSim.reconcile,
        },
      },
    });
    console.log(
      `Healed ${inst.user?.platformHandle ?? inst.userId}: cleared orphan paper position ${exchangeQty.toFixed(5)} BTC`,
    );
  }

  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
