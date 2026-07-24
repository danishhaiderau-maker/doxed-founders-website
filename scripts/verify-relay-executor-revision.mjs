#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { PrismaClient } from '@prisma/client';
import { classifyRelaySourceRevision } from './relay-revision-policy.mjs';

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
const revisionProofCache = new Map();

async function inspectDescendant(expectedRevision, observedRevision) {
  execFileSync(
    'git',
    ['fetch', '--no-tags', '--depth=64', 'origin', observedRevision],
    { stdio: 'ignore' },
  );
  let isDescendant = true;
  try {
    execFileSync(
      'git',
      ['merge-base', '--is-ancestor', expectedRevision, observedRevision],
      { stdio: 'ignore' },
    );
  } catch {
    isDescendant = false;
  }
  const changedFiles = isDescendant
    ? execFileSync(
        'git',
        ['diff', '--name-only', `${expectedRevision}..${observedRevision}`],
        { encoding: 'utf8' },
      )
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
    : [];
  return { isDescendant, changedFiles };
}

async function classifyObservedRevision(observedRevision) {
  if (!revisionProofCache.has(observedRevision)) {
    revisionProofCache.set(
      observedRevision,
      classifyRelaySourceRevision({
        expected,
        observed: observedRevision,
        inspectDescendant,
      }).catch(() => ({
        accepted: false,
        mode: 'inspection-failed',
        changedFiles: [],
      })),
    );
  }
  return revisionProofCache.get(observedRevision);
}

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
    const revisionClassifications = new Map();
    for (const health of currentWorkers) {
      const observedRevision = String(health?.sourceRevision ?? '').trim();
      revisionClassifications.set(
        observedRevision,
        await classifyObservedRevision(observedRevision),
      );
    }
    const matched =
      currentWorkers.length > 0 &&
      ownerIds.size === 1 &&
      currentWorkers.every(
        (health) =>
          revisionClassifications.get(
            String(health?.sourceRevision ?? '').trim(),
          )?.accepted === true &&
          health?.healthy === true &&
          health?.status === 'RUNNING' &&
          health?.executionEnabled === true &&
          health?.timeoutCount === 0 &&
          String(health?.ownerId ?? '').trim() !== '',
      );
    if (matched) {
      const observedRevision = String(
        currentWorkers[0]?.sourceRevision ?? '',
      ).trim();
      const revisionProof = revisionClassifications.get(observedRevision);
      console.log(
        `Relay executor owner ${[...ownerIds][0]} is fresh, enabled, RUNNING, and healthy ` +
        `on ${revisionProof.mode} revision ${observedRevision} (required safety ancestor ${expected})`,
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
