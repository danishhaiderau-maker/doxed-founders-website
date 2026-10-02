import assert from 'node:assert/strict';
import test from 'node:test';

import {
  APPLY_CONFIRMATION,
  normalizePausedRelayMode,
  parseArgs,
} from './normalize-paused-relay-mode.mjs';


const target = {
  userId: 'user-1',
  instanceId: 'instance-1',
  agentId: 'agent-1',
  provider: 'bitfinex',
  expectedUpdatedAt: '2026-09-20T00:00:00.000Z',
};

function row(overrides = {}) {
  return {
    id: target.instanceId,
    userId: target.userId,
    agentId: target.agentId,
    exchangeProvider: target.provider,
    status: 'PAUSED',
    dashboardState: {
      unrelated: { retained: true },
      relayExecutionMode: null,
      relayArmedAt: null,
      realTradingConfirmedAt: null,
      liveDeskSessionStartedAt: null,
    },
    updatedAt: new Date(target.expectedUpdatedAt),
    ...overrides,
  };
}

function fakePrisma({ stored = row(), participants = 0, updateCount = 1 } = {}) {
  const calls = [];
  const tx = {
    tradingAgentInstance: {
      findUnique: async (query) => {
        calls.push(['findUnique', query]);
        return stored;
      },
      updateMany: async (query) => {
        calls.push(['updateMany', query]);
        return { count: updateCount };
      },
    },
    signalCycleParticipant: {
      count: async (query) => {
        calls.push(['participantCount', query]);
        return participants;
      },
    },
  };
  return {
    calls,
    $transaction: async (callback, options) => {
      calls.push(['transaction', options]);
      return callback(tx);
    },
  };
}

test('default is a serializable dry run and performs no update', async () => {
  const prisma = fakePrisma();
  const result = await normalizePausedRelayMode(prisma, target);
  assert.equal(result.status, 'DRY_RUN');
  assert.equal(result.apply, false);
  assert.equal(prisma.calls.some(([name]) => name === 'updateMany'), false);
  assert.deepEqual(prisma.calls[0], ['transaction', { isolationLevel: 'Serializable' }]);
});

test('apply uses full identity, status, updatedAt and dashboardState CAS and changes one field', async () => {
  const stored = row();
  const prisma = fakePrisma({ stored });
  const result = await normalizePausedRelayMode(
    prisma, target, { apply: true, confirmation: APPLY_CONFIRMATION },
  );
  assert.equal(result.status, 'APPLIED');
  const update = prisma.calls.find(([name]) => name === 'updateMany')[1];
  assert.deepEqual(update.where, {
    id: stored.id,
    userId: stored.userId,
    agentId: stored.agentId,
    exchangeProvider: stored.exchangeProvider,
    status: stored.status,
    updatedAt: stored.updatedAt,
    dashboardState: { equals: stored.dashboardState },
  });
  assert.deepEqual(update.data.dashboardState, {
    ...stored.dashboardState,
    relayExecutionMode: 'PAUSED',
  });
  assert.deepEqual(stored.dashboardState.unrelated, { retained: true });
});

test('participant proof is scoped to exact user and agent', async () => {
  const prisma = fakePrisma();
  await normalizePausedRelayMode(prisma, target);
  const query = prisma.calls.find(([name]) => name === 'participantCount')[1];
  assert.deepEqual(query.where, {
    userId: target.userId,
    status: { in: ['OPEN', 'PENDING_ENTRY'] },
    cycle: { agentId: target.agentId },
  });
});

test('wrong explicit target identity is rejected before update', async () => {
  const prisma = fakePrisma({ stored: row({ userId: 'different-user' }) });
  await assert.rejects(
    normalizePausedRelayMode(prisma, target, { apply: true, confirmation: APPLY_CONFIRMATION }),
    /identity mismatch/,
  );
  assert.equal(prisma.calls.some(([name]) => name === 'updateMany'), false);
});

test('newer updatedAt than the approved target observation is rejected', async () => {
  const prisma = fakePrisma({
    stored: row({ updatedAt: new Date('2026-09-20T00:00:01.000Z') }),
  });
  await assert.rejects(normalizePausedRelayMode(prisma, target), /identity mismatch/);
});

test('active relay is rejected', async () => {
  const prisma = fakePrisma({ stored: row({ status: 'ACTIVE' }) });
  await assert.rejects(normalizePausedRelayMode(prisma, target), /status must be PAUSED/);
});

for (const field of ['relayArmedAt', 'realTradingConfirmedAt']) {
  test(`${field} rejects armed or confirmed target`, async () => {
    const prisma = fakePrisma({
      stored: row({ dashboardState: { ...row().dashboardState, [field]: '2026-09-20T00:00:00Z' } }),
    });
    await assert.rejects(normalizePausedRelayMode(prisma, target), new RegExp(field));
  });
}

test('live desk session is rejected', async () => {
  const prisma = fakePrisma({
    stored: row({
      dashboardState: {
        ...row().dashboardState,
        liveDeskSessionStartedAt: '2026-09-20T00:00:00Z',
      },
    }),
  });
  await assert.rejects(
    normalizePausedRelayMode(prisma, target),
    /liveDeskSessionStartedAt/,
  );
});

test('OPEN or PENDING_ENTRY participants reject normalization', async () => {
  const prisma = fakePrisma({ participants: 1 });
  await assert.rejects(normalizePausedRelayMode(prisma, target), /OPEN or PENDING_ENTRY/);
});

for (const count of [0, 2]) {
  test(`CAS count ${count} rejects and transaction cannot report success`, async () => {
    const prisma = fakePrisma({ updateCount: count });
    await assert.rejects(
      normalizePausedRelayMode(
        prisma, target, { apply: true, confirmation: APPLY_CONFIRMATION },
      ),
      new RegExp(`updated ${count} rows`),
    );
  });
}

test('non-null relay mode is never overwritten', async () => {
  const prisma = fakePrisma({
    stored: row({ dashboardState: { ...row().dashboardState, relayExecutionMode: 'PAUSED' } }),
  });
  await assert.rejects(normalizePausedRelayMode(prisma, target), /null or missing/);
});

test('CLI defaults to dry run and apply needs the exact confirmation', () => {
  const base = [
    '--user-id', target.userId,
    '--instance-id', target.instanceId,
    '--agent-id', target.agentId,
    '--provider', target.provider,
    '--expected-updated-at', target.expectedUpdatedAt,
  ];
  assert.equal(parseArgs(base).apply, false);
  assert.equal(parseArgs([...base, '--dry-run']).apply, false);
  assert.throws(() => parseArgs([...base, '--apply']), /requires --confirmation/);
  assert.throws(
    () => parseArgs([...base, '--apply', '--confirmation', 'WRONG']),
    /requires --confirmation/,
  );
  assert.equal(
    parseArgs([
      ...base, '--apply', '--confirmation', APPLY_CONFIRMATION,
    ]).apply,
    true,
  );
});

test('CLI rejects multiple or non-Bitfinex target declarations', () => {
  const base = [
    '--user-id', target.userId,
    '--instance-id', target.instanceId,
    '--agent-id', target.agentId,
    '--provider', target.provider,
    '--expected-updated-at', target.expectedUpdatedAt,
  ];
  assert.throws(() => parseArgs([...base, '--user-id', 'user-2']), /duplicate/);
  assert.throws(
    () => parseArgs(base.map((value) => value === 'bitfinex' ? 'kraken' : value)),
    /provider must equal bitfinex/,
  );
});
