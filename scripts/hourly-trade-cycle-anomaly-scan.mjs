#!/usr/bin/env node
/**
 * Scan trade_cycle_audit.jsonl + DB for missing stages / capacity violations.
 * Usage: node scripts/hourly-trade-cycle-anomaly-scan.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient, SignalCycleStatus } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

function isCapacityViolation(open, pending, limit) {
  return open + pending > limit;
}

const root = joinRoot();
const auditPath = path.join(root, 'logs', 'trade_cycle_audit.jsonl');
const prisma = new PrismaClient();

function joinRoot() {
  return path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
}

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

function readAuditRows() {
  if (!fs.existsSync(auditPath)) return [];
  return fs
    .readFileSync(auditPath, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

async function main() {
  const anomalies = [];
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) {
    console.log('No conservative-btc agent');
    return;
  }

  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
  });

  for (const inst of instances) {
    const dash = inst.dashboardState ?? {};
    const cap = dash.copyRelayCapacity;
    if (cap && isCapacityViolation(cap.activeOpen, cap.activePending, cap.capacityLimit)) {
      anomalies.push({
        kind: 'CAPACITY_VIOLATION',
        userId: inst.userId,
        detail: `OPEN=${cap.activeOpen} PENDING=${cap.activePending} limit=${cap.capacityLimit}`,
      });
    }
    const rec = dash.copyRelayReconcile;
    if (rec?.alert) {
      anomalies.push({
        kind: 'RECONCILE_ALERT',
        userId: inst.userId,
        detail: `Δ=${rec.deltaBtc} exchange=${rec.exchangePositionQty} ledger=${rec.ledgerOpenQty}`,
      });
    }
  }

  const filledNoOpen = await prisma.signalCycleParticipant.findMany({
    where: {
      status: SignalCycleStatus.PENDING_ENTRY,
      events: { some: { eventType: 'FILLED' } },
      cycle: { agentId: agent.id },
    },
    take: 50,
  });
  for (const p of filledNoOpen) {
    anomalies.push({
      kind: 'FILLED_NOT_OPEN',
      userId: p.userId,
      participantId: p.id,
      detail: 'FILLED event but participant still PENDING_ENTRY',
    });
  }

  const openNoExit = await prisma.signalCycleParticipant.findMany({
    where: {
      status: SignalCycleStatus.OPEN,
      updatedAt: { lt: new Date(Date.now() - 7 * 24 * 3600_000) },
      cycle: { agentId: agent.id, status: { in: ['CLOSED', 'EXPIRED'] } },
    },
    take: 20,
  });
  for (const p of openNoExit) {
    anomalies.push({
      kind: 'OPEN_AFTER_CYCLE_CLOSED',
      userId: p.userId,
      participantId: p.id,
      detail: 'Participant OPEN while cycle CLOSED/EXPIRED',
    });
  }

  const pausedEntry = readAuditRows().filter(
    (r) =>
      r.stage === 'ORDER_PLACED' &&
      new Date(r.ts).getTime() > Date.now() - 3600_000,
  );
  for (const inst of instances) {
    if (inst.status !== 'PAUSED') continue;
    const userOrders = pausedEntry.filter((r) => r.userId === inst.userId);
    if (userOrders.length) {
      anomalies.push({
        kind: 'PAUSED_ENTRY_LEAK',
        userId: inst.userId,
        detail: `${userOrders.length} ORDER_PLACED audit rows while instance PAUSED (last hour)`,
      });
    }
  }

  console.log(`\n=== Trade cycle anomaly scan (${new Date().toISOString()}) ===\n`);
  if (!anomalies.length) {
    console.log('No anomalies detected.');
  } else {
    for (const a of anomalies) console.log(JSON.stringify(a));
    process.exitCode = 1;
  }
  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
