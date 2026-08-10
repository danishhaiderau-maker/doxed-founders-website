#!/usr/bin/env node
import { PrismaClient } from '@prisma/client';

const instanceId = String(process.env.RELAY_INSTANCE_ID ?? '').trim();
const expectedRevision = String(process.env.EXPECTED_EXECUTOR_REVISION ?? '').trim();
if (process.env.CONFIRM_RELAY_REARM !== 'ARM_NEXT_FRESH_ONLY') {
  throw new Error('Refusing: CONFIRM_RELAY_REARM must equal ARM_NEXT_FRESH_ONLY');
}
if (!instanceId || !expectedRevision || !process.env.DATABASE_URL) {
  throw new Error('RELAY_INSTANCE_ID, EXPECTED_EXECUTOR_REVISION, and DATABASE_URL are required');
}

const prisma = new PrismaClient();
try {
  const instance = await prisma.tradingAgentInstance.findUnique({
    where: { id: instanceId },
    include: { agent: { select: { slug: true } } },
  });
  if (!instance || instance.agent.slug !== 'conservative-btc') {
    throw new Error('Refusing: target Conservative BTC instance is missing');
  }
  if (instance.status !== 'PAUSED') {
    throw new Error(`Refusing: instance status is ${instance.status}, expected PAUSED`);
  }
  const dash = instance.dashboardState ?? {};
  if (dash.copyRelaySim?.active === true) throw new Error('Refusing: relay sim is active');

  const executor = dash.relayExecutor;
  const observedAtMs = Date.parse(String(executor?.observedAt ?? ''));
  const executorAgeMs = Number.isFinite(observedAtMs) ? Date.now() - observedAtMs : Infinity;
  if (
    executorAgeMs < 0 ||
    executorAgeMs > 30_000 ||
    executor?.healthy !== true ||
    executor?.status !== 'RUNNING' ||
    executor?.executionEnabled !== true ||
    Number(executor?.timeoutCount ?? -1) !== 0 ||
    String(executor?.sourceRevision ?? '') !== expectedRevision
  ) {
    throw new Error(`Refusing: exact healthy executor is not current ${JSON.stringify({
      executorAgeMs: Number.isFinite(executorAgeMs) ? executorAgeMs : null,
      healthy: executor?.healthy ?? null,
      status: executor?.status ?? null,
      executionEnabled: executor?.executionEnabled ?? null,
      timeoutCount: executor?.timeoutCount ?? null,
      sourceRevision: String(executor?.sourceRevision ?? '').slice(0, 12) || null,
    })}`);
  }

  const virtualExposure = await prisma.signalCycleParticipant.count({
    where: {
      userId: instance.userId,
      status: { in: ['PENDING_ENTRY', 'OPEN'] },
      cycle: { agentId: instance.agentId },
    },
  });
  if (virtualExposure !== 0) {
    throw new Error(`Refusing: ${virtualExposure} PENDING_ENTRY/OPEN relay lot(s) remain`);
  }

  const armedAt = new Date().toISOString();
  await prisma.tradingAgentInstance.update({
    where: { id: instanceId },
    data: {
      status: 'ACTIVE',
      lastError: null,
      dashboardState: {
        ...dash,
        relayExecutionMode: 'LIVE',
        realTradingConfirmedAt: armedAt,
        relayArmedAt: armedAt,
        liveDeskSessionStartedAt: armedAt,
        positionMismatchAlert: null,
        positionMismatchAlertAcked: true,
        relayEntryPolicy: 'NEXT_FRESH_ONLY',
        relayExecutorAtArm: executor,
      },
    },
  });

  console.log(JSON.stringify({
    instanceId,
    status: 'ACTIVE',
    relayEntryPolicy: 'NEXT_FRESH_ONLY',
    relayArmedAt: armedAt,
    executorRevision: expectedRevision.slice(0, 12),
    virtualExposure,
  }));
} finally {
  await prisma.$disconnect();
}
