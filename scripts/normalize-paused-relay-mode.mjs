#!/usr/bin/env node

import { pathToFileURL } from 'node:url';


export const APPLY_CONFIRMATION = 'NORMALIZE_ONE_PAUSED_RELAY_MODE';

function requiredId(value, label) {
  const normalized = String(value ?? '').trim();
  if (!normalized || normalized.length > 256 || /[\u0000-\u001f\u007f]/.test(normalized)) {
    throw new Error(`${label} must be one explicit non-empty identifier`);
  }
  return normalized;
}

export function validateTarget(target) {
  const expectedUpdatedAt = requiredId(
    target?.expectedUpdatedAt,
    'expectedUpdatedAt',
  );
  const parsedUpdatedAt = new Date(expectedUpdatedAt);
  if (
    !Number.isFinite(parsedUpdatedAt.getTime())
    || parsedUpdatedAt.toISOString() !== expectedUpdatedAt
  ) {
    throw new Error('expectedUpdatedAt must be an exact canonical ISO timestamp');
  }
  const normalized = {
    userId: requiredId(target?.userId, 'userId'),
    instanceId: requiredId(target?.instanceId, 'instanceId'),
    agentId: requiredId(target?.agentId, 'agentId'),
    provider: String(target?.provider ?? '').trim(),
    expectedUpdatedAt,
  };
  if (normalized.provider !== 'bitfinex') {
    throw new Error('provider must equal bitfinex');
  }
  return normalized;
}

function dashboardObject(value) {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('target dashboardState must be an existing JSON object');
  }
  return value;
}

export async function normalizePausedRelayMode(
  prisma,
  target,
  { apply = false, confirmation = '' } = {},
) {
  const expected = validateTarget(target);
  if (apply && confirmation !== APPLY_CONFIRMATION) {
    throw new Error(`apply requires --confirmation ${APPLY_CONFIRMATION}`);
  }
  if (!prisma?.$transaction) {
    throw new Error('Prisma transaction client is required');
  }
  return prisma.$transaction(async (tx) => {
    const row = await tx.tradingAgentInstance.findUnique({
      where: { id: expected.instanceId },
      select: {
        id: true,
        userId: true,
        agentId: true,
        exchangeProvider: true,
        status: true,
        dashboardState: true,
        updatedAt: true,
      },
    });
    if (
      !row
      || row.id !== expected.instanceId
      || row.userId !== expected.userId
      || row.agentId !== expected.agentId
      || row.exchangeProvider !== expected.provider
      || !(row.updatedAt instanceof Date)
      || row.updatedAt.toISOString() !== expected.expectedUpdatedAt
    ) {
      throw new Error('exact relay target identity mismatch');
    }
    if (row.status !== 'PAUSED') {
      throw new Error('target relay status must be PAUSED');
    }
    const dashboardState = dashboardObject(row.dashboardState);
    if (dashboardState.relayExecutionMode != null) {
      throw new Error('relayExecutionMode must be null or missing');
    }
    for (const key of [
      'relayArmedAt',
      'realTradingConfirmedAt',
      'liveDeskSessionStartedAt',
    ]) {
      if (dashboardState[key] != null) {
        throw new Error(`${key} must be null or missing`);
      }
    }
    const openParticipants = await tx.signalCycleParticipant.count({
      where: {
        userId: expected.userId,
        status: { in: ['OPEN', 'PENDING_ENTRY'] },
        cycle: { agentId: expected.agentId },
      },
    });
    if (openParticipants !== 0) {
      throw new Error('target has OPEN or PENDING_ENTRY participants');
    }
    const nextDashboardState = {
      ...dashboardState,
      relayExecutionMode: 'PAUSED',
    };
    if (!apply) {
      return {
        status: 'DRY_RUN',
        apply: false,
        target: expected,
        openParticipants,
        wouldSet: { relayExecutionMode: 'PAUSED' },
      };
    }
    const updated = await tx.tradingAgentInstance.updateMany({
      where: {
        id: row.id,
        userId: row.userId,
        agentId: row.agentId,
        exchangeProvider: row.exchangeProvider,
        status: row.status,
        updatedAt: row.updatedAt,
        dashboardState: { equals: row.dashboardState },
      },
      data: { dashboardState: nextDashboardState },
    });
    if (updated?.count !== 1) {
      throw new Error(`relay mode CAS updated ${String(updated?.count)} rows; expected exactly 1`);
    }
    return {
      status: 'APPLIED',
      apply: true,
      target: expected,
      openParticipants,
      changed: { relayExecutionMode: 'PAUSED' },
    };
  }, { isolationLevel: 'Serializable' });
}

export function parseArgs(argv) {
  const values = new Map();
  let apply = false;
  let dryRun = false;
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--apply' || arg === '--dry-run') {
      if (arg === '--apply') apply = true;
      else dryRun = true;
      continue;
    }
    if (![
      '--user-id', '--instance-id', '--agent-id', '--provider',
      '--expected-updated-at', '--confirmation',
    ].includes(arg)) {
      throw new Error(`unknown argument: ${arg}`);
    }
    if (values.has(arg)) throw new Error(`duplicate argument: ${arg}`);
    const value = argv[index + 1];
    if (value == null || value.startsWith('--')) {
      throw new Error(`missing value for ${arg}`);
    }
    values.set(arg, value);
    index += 1;
  }
  if (apply && dryRun) throw new Error('--apply and --dry-run are mutually exclusive');
  const confirmation = values.get('--confirmation') ?? '';
  if (!apply && confirmation) throw new Error('--confirmation is accepted only with --apply');
  if (apply && confirmation !== APPLY_CONFIRMATION) {
    throw new Error(`--apply requires --confirmation ${APPLY_CONFIRMATION}`);
  }
  return {
    target: validateTarget({
      userId: values.get('--user-id'),
      instanceId: values.get('--instance-id'),
      agentId: values.get('--agent-id'),
      provider: values.get('--provider'),
      expectedUpdatedAt: values.get('--expected-updated-at'),
    }),
    apply,
    confirmation,
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const prismaPackage = await import('../node_modules/.prisma/client/default.js');
  const prisma = new prismaPackage.default.PrismaClient();
  try {
    const receipt = await normalizePausedRelayMode(prisma, options.target, options);
    process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
  } finally {
    await prisma.$disconnect();
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
