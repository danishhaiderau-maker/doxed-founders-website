#!/usr/bin/env node
/** Pause/disarm one exact Bitfinex relay and expire its pending legs for deploy. */
import fs from 'node:fs';
import path from 'node:path';
import { PrismaClient, SignalCycleStatus } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

if (process.env.CONFIRM_DEPLOY_FORCE_FLAT !== 'PAUSE_EXACT_INSTANCE') {
  throw new Error('Refusing: CONFIRM_DEPLOY_FORCE_FLAT must equal PAUSE_EXACT_INSTANCE');
}
const instanceId = String(process.env.RELAY_INSTANCE_ID ?? '').trim();
if (!instanceId) throw new Error('RELAY_INSTANCE_ID is required');

const neonPath = path.join(getVaultDir(), '.env.neon');
if (fs.existsSync(neonPath)) {
  for (const raw of fs.readFileSync(neonPath, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const splitAt = line.indexOf('=');
    if (splitAt < 1) continue;
    const key = line.slice(0, splitAt).trim();
    let value = line.slice(splitAt + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

const prisma = new PrismaClient();
try {
  const instance = await prisma.tradingAgentInstance.findUnique({
    where: { id: instanceId },
    include: { agent: { select: { slug: true } } },
  });
  if (!instance || instance.agent.slug !== 'conservative-btc' || instance.exchangeProvider !== 'bitfinex') {
    throw new Error('Refusing: exact Conservative BTC Bitfinex instance is missing');
  }

  const pending = await prisma.signalCycleParticipant.findMany({
    where: {
      userId: instance.userId,
      status: SignalCycleStatus.PENDING_ENTRY,
      cycle: { agentId: instance.agentId },
    },
    select: {
      id: true,
      cycleId: true,
      cycle: { select: { tradeId: true } },
    },
  });

  await prisma.$transaction(async (tx) => {
    await tx.tradingAgentInstance.update({
      where: { id: instance.id },
      data: {
        status: 'PAUSED',
        lastError: 'Operator deploy boundary: paused, disarmed, and pending legs expired.',
        dashboardState: {
          ...(instance.dashboardState ?? {}),
          relayExecutionMode: 'PAUSED',
          relayArmedAt: null,
          realTradingConfirmedAt: null,
          relayEntryPolicy: 'PAUSED_FOR_DEPLOY',
        },
      },
    });
    for (const row of pending) {
      await tx.signalCycleParticipant.update({
        where: { id: row.id },
        data: { status: SignalCycleStatus.EXPIRED },
      });
      await tx.signalCycleEvent.create({
        data: {
          cycleId: row.cycleId,
          participantId: row.id,
          eventType: 'EXPIRED',
          payload: {
            exit_reason: 'USER_DEPLOY_FORCE_FLAT',
            source: 'operator_deploy_boundary',
            trade_id: row.cycle.tradeId,
          },
        },
      });
    }
  });

  console.log(JSON.stringify({
    instanceId: instance.id,
    status: 'PAUSED',
    relayExecutionMode: 'PAUSED',
    expiredPendingTradeIds: pending.map((row) => row.cycle.tradeId),
  }));
} finally {
  await prisma.$disconnect();
}
