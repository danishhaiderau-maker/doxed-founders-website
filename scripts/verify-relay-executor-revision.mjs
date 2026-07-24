#!/usr/bin/env node
import { PrismaClient } from '@prisma/client';

const expected = String(process.env.EXPECTED_SOURCE_REVISION ?? '').trim();
const timeoutMs = Number(process.env.RELAY_REVISION_TIMEOUT_MS ?? 120_000);
const maxAgeMs = Number(process.env.RELAY_EXECUTOR_MAX_AGE_MS ?? 30_000);
if (!expected) throw new Error('EXPECTED_SOURCE_REVISION is required');
if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is required');
if (!Number.isFinite(maxAgeMs) || maxAgeMs <= 0) {
  throw new Error('RELAY_EXECUTOR_MAX_AGE_MS must be a positive number');
}

const prisma = new PrismaClient();
const deadline = Date.now() + timeoutMs;
let lastObserved = [];

try {
  while (Date.now() < deadline) {
    const agent = await prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
      select: { id: true },
    });
    if (!agent) throw new Error('conservative-btc agent missing');
    const instances = await prisma.tradingAgentInstance.findMany({
      where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
      select: { dashboardState: true },
    });
    const workers = instances
      .map((instance) => instance.dashboardState?.relayExecutor)
      .filter((health) => health?.serviceRole === 'executor-worker');
    const nowMs = Date.now();
    const currentWorkers = workers.filter((health) => {
      const observedAtMs = Date.parse(String(health?.observedAt ?? ''));
      const ageMs = nowMs - observedAtMs;
      return Number.isFinite(ageMs) && ageMs >= 0 && ageMs <= maxAgeMs;
    });
    const ownerIds = new Set(
      currentWorkers
        .map((health) => String(health?.ownerId ?? '').trim())
        .filter(Boolean),
    );
    lastObserved = currentWorkers.map((health) => ({
      sourceRevision: health?.sourceRevision ?? null,
      healthy: health?.healthy === true,
      status: health?.status ?? null,
      executionEnabled: health?.executionEnabled === true,
      timeoutCount: health?.timeoutCount ?? null,
      observedAt: health?.observedAt ?? null,
      ownerPresent: String(health?.ownerId ?? '').trim() !== '',
    }));
    const matched =
      currentWorkers.length > 0 &&
      ownerIds.size === 1 &&
      currentWorkers.every(
        (health) =>
          health?.sourceRevision === expected &&
          health?.healthy === true &&
          health?.status === 'RUNNING' &&
          health?.executionEnabled === true &&
          health?.timeoutCount === 0 &&
          String(health?.ownerId ?? '').trim() !== '',
      );
    if (matched) {
      console.log(
        `Relay executor owner ${[...ownerIds][0]} is fresh, enabled, RUNNING, and healthy on exact revision ${expected}`,
      );
      process.exitCode = 0;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 5_000));
  }
  if (process.exitCode == null) {
    throw new Error(
      `Relay executor did not report exact healthy revision ${expected}; ` +
      `observed=${JSON.stringify(lastObserved)}`,
    );
  }
} finally {
  await prisma.$disconnect();
}
