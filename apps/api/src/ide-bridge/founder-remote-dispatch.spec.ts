import assert from 'node:assert/strict';
import test from 'node:test';
import {
  FOUNDER_REMOTE_CAPABILITY,
  FOUNDER_REMOTE_CAPABILITY_VERSION,
  FOUNDER_REMOTE_CLAIMED,
  FOUNDER_REMOTE_CLAIM_TTL_MS,
  FOUNDER_REMOTE_COMPLETE,
  FOUNDER_REMOTE_FAILED,
  FOUNDER_REMOTE_PENDING,
  FOUNDER_REMOTE_PROVIDER,
  FounderRemoteDispatchError,
  FounderRemoteDispatchService,
  founderRemoteEnvelopeHash,
  type FounderRemoteDispatchCreateData,
  type FounderRemoteDispatchRepository,
  type FounderRemoteDispatchRow,
  type FounderRemoteNodeAssertion,
  type FounderRemoteResolvedTarget,
  type FounderRemoteWhere,
} from './founder-remote-dispatch';

function matches(row: FounderRemoteDispatchRow, where: FounderRemoteWhere): boolean {
  return Object.entries(where).every(([key, expected]) => {
    const actual = row[key as keyof FounderRemoteDispatchRow];
    if (expected && typeof expected === 'object' && 'gt' in expected) {
      const lowerBound = (expected as { gt: Date }).gt;
      return actual instanceof Date && actual.getTime() > lowerBound.getTime();
    }
    if (actual instanceof Date && expected instanceof Date) return actual.getTime() === expected.getTime();
    return actual === expected;
  });
}

function hashRow(row: FounderRemoteDispatchRow, patch: Partial<FounderRemoteDispatchRow> = {}): string {
  const value = { ...row, ...patch };
  if (!value.targetNodeId || typeof value.capabilityVersion !== 'number' || !value.targetCapability) {
    throw new Error('test row is missing its remote envelope');
  }
  return founderRemoteEnvelopeHash({
    id: value.id,
    sessionId: value.sessionId,
    ideProvider: value.ideProvider,
    targetNodeId: value.targetNodeId,
    capabilityVersion: value.capabilityVersion,
    targetCapability: value.targetCapability,
    prompt: value.prompt,
  });
}

class MemoryRepository implements FounderRemoteDispatchRepository {
  readonly rows: FounderRemoteDispatchRow[] = [];

  async create({ data }: { data: FounderRemoteDispatchCreateData }): Promise<FounderRemoteDispatchRow> {
    const row = { ...data, createdAt: data.createdAt ?? new Date() } as FounderRemoteDispatchRow;
    this.rows.push(row);
    return row;
  }

  async findFirst({ where }: { where: FounderRemoteWhere }): Promise<FounderRemoteDispatchRow | null> {
    return this.rows.find((row) => matches(row, where)) ?? null;
  }

  async findMany({
    where,
    take,
  }: {
    where: FounderRemoteWhere;
    orderBy?: FounderRemoteWhere;
    take?: number;
  }): Promise<FounderRemoteDispatchRow[]> {
    return this.rows.filter((row) => matches(row, where)).slice(0, take);
  }

  async updateMany({
    where,
    data,
  }: {
    where: FounderRemoteWhere;
    data: Partial<FounderRemoteDispatchRow>;
  }): Promise<{ count: number }> {
    let count = 0;
    for (const row of this.rows) {
      if (!matches(row, where)) continue;
      Object.assign(row, data);
      count += 1;
    }
    return { count };
  }
}

type HarnessOverrides = {
  resolveTarget?: (userId: string, sessionId: string, nodeId: string) => Promise<FounderRemoteResolvedTarget | null>;
  assertNode?: (userId: string, nodeId: string) => Promise<FounderRemoteNodeAssertion>;
};

function harness(overrides: HarnessOverrides = {}) {
  const repository = new MemoryRepository();
  let nowMs = Date.parse('2026-09-21T00:00:00.000Z');
  let id = 0;
  let randomCall = 0;
  const resolveTarget = overrides.resolveTarget ?? (async (_userId, sessionId, nodeId) => ({
    sessionId,
    targetNodeId: nodeId,
    ideProvider: FOUNDER_REMOTE_PROVIDER,
  }));
  const assertNode = overrides.assertNode ?? (async (_userId, nodeId) => ({
    nodeId,
    ideProvider: FOUNDER_REMOTE_PROVIDER,
    capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
    capabilities: [FOUNDER_REMOTE_CAPABILITY],
  }));
  const service = new FounderRemoteDispatchService({
    repository,
    resolveTarget,
    assertNode,
    now: () => new Date(nowMs),
    idFactory: () => `remote-${++id}`,
    randomBytes: (size) => new Uint8Array(size).fill(++randomCall),
  });
  return {
    repository,
    service,
    advance(milliseconds: number) {
      nowMs += milliseconds;
    },
  };
}

const claimBody = {
  targetNodeId: 'node-a',
  ideProvider: FOUNDER_REMOTE_PROVIDER,
  capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
  targetCapability: FOUNDER_REMOTE_CAPABILITY,
};

test('expired pending and claimed requests expose terminal expiry without reclaiming', async () => {
  const pending = harness();
  const first = await pending.service.create('user-a', 'session-a', 'test', 'node-a');
  pending.advance(16 * 60_000);
  const pendingStatus = await pending.service.status('user-a', first.id);
  assert.equal(pendingStatus.status, 'REMOTE_EXPIRED');
  assert.match(pendingStatus.error!, /before a desktop claim/);
  assert.equal(pending.repository.rows[0].status, FOUNDER_REMOTE_PENDING);

  const claimed = harness();
  const second = await claimed.service.create('user-a', 'session-a', 'test', 'node-a');
  await claimed.service.claim('user-a', 'node-a', second.id, claimBody);
  claimed.advance(FOUNDER_REMOTE_CLAIM_TTL_MS + 1);
  const claimStatus = await claimed.service.status('user-a', second.id);
  assert.equal(claimStatus.status, 'REMOTE_EXPIRED');
  assert.match(claimStatus.error!, /outcome is unknown/);
  assert.equal(claimStatus.delivered, false);
  assert.equal(claimed.repository.rows[0].status, FOUNDER_REMOTE_CLAIMED);
});

async function expectCode(operation: Promise<unknown>, code: string): Promise<void> {
  await assert.rejects(operation, (error: unknown) => {
    assert.ok(error instanceof FounderRemoteDispatchError);
    assert.equal(error.code, code);
    return true;
  });
}

test('create cleans text exactly, binds the seven-field envelope, and never uses legacy PENDING', async () => {
  const { repository, service } = harness();
  const created = await service.create('user-a', 'session-a', '  first\r\nsecond\u0000  ', 'node-a');
  const row = repository.rows[0];

  assert.equal(created.prompt, 'first\nsecond');
  assert.equal(row.status, FOUNDER_REMOTE_PENDING);
  assert.notEqual(row.status, 'PENDING');
  assert.equal(row.envelopeHash, hashRow(row));
  assert.equal(row.claimTokenHash, null);
  assert.deepEqual(
    [created.id, created.sessionId, created.ideProvider, created.targetNodeId, created.capabilityVersion, created.targetCapability, created.prompt],
    ['remote-1', 'session-a', FOUNDER_REMOTE_PROVIDER, 'node-a', 1, FOUNDER_REMOTE_CAPABILITY, 'first\nsecond'],
  );
  await expectCode(service.create('user-a', 'session-a', 'x'.repeat(12_001), 'node-a'), 'PROMPT_TOO_LARGE');
});

test('create fails closed for unknown or mismatched session, node, provider, and capability', async (t) => {
  const targetCases: Array<[string, FounderRemoteResolvedTarget | null]> = [
    ['unknown target', null],
    ['wrong session', { sessionId: 'session-b', targetNodeId: 'node-a', ideProvider: FOUNDER_REMOTE_PROVIDER }],
    ['wrong node', { sessionId: 'session-a', targetNodeId: 'node-b', ideProvider: FOUNDER_REMOTE_PROVIDER }],
    ['wrong provider', { sessionId: 'session-a', targetNodeId: 'node-a', ideProvider: 'cursor' }],
  ];
  for (const [name, target] of targetCases) {
    await t.test(name, async () => {
      const { service } = harness({ resolveTarget: async () => target });
      await expectCode(service.create('user-a', 'session-a', 'build it', 'node-a'), 'TARGET_NOT_FOUND');
    });
  }

  const nodeCases: Array<[string, Partial<FounderRemoteNodeAssertion>]> = [
    ['wrong asserted node', { nodeId: 'node-b' }],
    ['wrong asserted provider', { ideProvider: 'cursor' }],
    ['wrong capability version', { capabilityVersion: 2 }],
    ['missing capability', { capabilities: [] }],
  ];
  for (const [name, patch] of nodeCases) {
    await t.test(name, async () => {
      const { service } = harness({
        assertNode: async (_userId, nodeId) => ({
          nodeId,
          ideProvider: FOUNDER_REMOTE_PROVIDER,
          capabilityVersion: 1,
          capabilities: [FOUNDER_REMOTE_CAPABILITY],
          ...patch,
        }),
      });
      await expectCode(service.create('user-a', 'session-a', 'build it', 'node-a'), 'CAPABILITY_REQUIRED');
    });
  }
});

test('pending is owner/node/capability scoped and excludes legacy dispatches', async () => {
  const { repository, service } = harness();
  await service.create('user-a', 'session-a', 'remote', 'node-a');
  const valid = repository.rows[0];
  repository.rows.push(
    { ...valid, id: 'legacy', status: 'PENDING', envelopeHash: null },
    { ...valid, id: 'other-user', userId: 'user-b', envelopeHash: hashRow(valid, { id: 'other-user', userId: 'user-b' }) },
    { ...valid, id: 'other-node', targetNodeId: 'node-b', envelopeHash: hashRow(valid, { id: 'other-node', targetNodeId: 'node-b' }) },
  );

  const pending = await service.pending('user-a', 'node-a');
  assert.deepEqual(pending.map((row) => row.id), [valid.id]);
  assert.doesNotMatch(JSON.stringify(pending), /claimToken/i);
});

test('claim validates body, refreshes target/capability, expires pending rows, and CAS permits one winner', async () => {
  const { repository, service, advance } = harness();
  const created = await service.create('user-a', 'session-a', 'remote', 'node-a');

  await expectCode(service.claim('user-a', 'node-a', created.id, { ...claimBody, targetNodeId: 'node-b' }), 'INVALID_CLAIM');
  await expectCode(service.claim('user-a', 'node-a', created.id, { ...claimBody, ideProvider: 'cursor' }), 'INVALID_CLAIM');
  await expectCode(service.claim('user-a', 'node-a', created.id, { ...claimBody, capabilityVersion: 2 }), 'INVALID_CLAIM');
  await expectCode(service.claim('user-a', 'node-a', created.id, { ...claimBody, targetCapability: 'other' }), 'INVALID_CLAIM');

  const attempts = await Promise.allSettled([
    service.claim('user-a', 'node-a', created.id, claimBody),
    service.claim('user-a', 'node-a', created.id, claimBody),
  ]);
  assert.equal(attempts.filter((attempt) => attempt.status === 'fulfilled').length, 1);
  assert.equal(attempts.filter((attempt) => attempt.status === 'rejected').length, 1);
  const claim = attempts.find((attempt): attempt is PromiseFulfilledResult<Awaited<ReturnType<typeof service.claim>>> => attempt.status === 'fulfilled')?.value;
  assert.ok(claim);
  assert.match(claim.claimToken, /^[A-Za-z0-9_-]{20,1024}\.[A-Za-z0-9_-]{20,128}$/);
  const row = repository.rows[0];
  assert.equal(row.status, FOUNDER_REMOTE_CLAIMED);
  assert.equal(row.claimTokenHash?.length, 64);
  assert.equal(JSON.stringify(row).includes(claim.claimToken), false);
  assert.equal(row.expiresAt?.getTime(), row.claimedAt!.getTime() + FOUNDER_REMOTE_CLAIM_TTL_MS);

  const expired = harness();
  const expiring = await expired.service.create('user-a', 'session-a', 'remote', 'node-a');
  expired.advance(16 * 60_000);
  await expectCode(expired.service.claim('user-a', 'node-a', expiring.id, claimBody), 'CLAIM_CONFLICT');

  const targetChanged = harness();
  const targetCreated = await targetChanged.service.create('user-a', 'session-a', 'remote', 'node-a');
  const originalRows = targetChanged.repository.rows;
  const rejecting = new FounderRemoteDispatchService({
    repository: targetChanged.repository,
    resolveTarget: async () => null,
    assertNode: async (_userId, nodeId) => ({
      nodeId,
      ideProvider: FOUNDER_REMOTE_PROVIDER,
      capabilityVersion: 1,
      capabilities: [FOUNDER_REMOTE_CAPABILITY],
    }),
    now: () => new Date('2026-09-21T00:01:00.000Z'),
  });
  await expectCode(rejecting.claim('user-a', 'node-a', targetCreated.id, claimBody), 'TARGET_NOT_FOUND');
  assert.equal(originalRows[0].status, FOUNDER_REMOTE_PENDING);

  const capabilityChanged = harness();
  const capabilityCreated = await capabilityChanged.service.create('user-a', 'session-a', 'remote', 'node-a');
  const incapable = new FounderRemoteDispatchService({
    repository: capabilityChanged.repository,
    resolveTarget: async (_userId, sessionId, nodeId) => ({
      sessionId,
      targetNodeId: nodeId,
      ideProvider: FOUNDER_REMOTE_PROVIDER,
    }),
    assertNode: async (_userId, nodeId) => ({
      nodeId,
      ideProvider: FOUNDER_REMOTE_PROVIDER,
      capabilityVersion: 1,
      capabilities: [],
    }),
    now: () => new Date('2026-09-21T00:01:00.000Z'),
  });
  await expectCode(incapable.claim('user-a', 'node-a', capabilityCreated.id, claimBody), 'CAPABILITY_REQUIRED');
});

test('completion requires the authenticated owner/node, active token, intact envelope, and one CAS winner', async () => {
  const { repository, service } = harness();
  const created = await service.create('user-a', 'session-a', 'remote', 'node-a');
  const claim = await service.claim('user-a', 'node-a', created.id, claimBody);
  const wrongToken = `Z${claim.claimToken.slice(1)}`;

  await expectCode(service.complete('user-b', 'node-a', created.id, { claimToken: claim.claimToken, result: 'ok' }), 'INVALID_CLAIM');
  await expectCode(service.complete('user-a', 'node-b', created.id, { claimToken: claim.claimToken, result: 'ok' }), 'INVALID_CLAIM');
  await expectCode(service.complete('user-a', 'node-a', created.id, { claimToken: wrongToken, result: 'ok' }), 'INVALID_CLAIM');
  await expectCode(service.complete('user-a', 'node-a', created.id, { claimToken: claim.claimToken, result: 'ok', error: 'bad' }), 'INVALID_COMPLETION');

  const attempts = await Promise.allSettled([
    service.complete('user-a', 'node-a', created.id, { claimToken: claim.claimToken, result: '  built\r\nwell\u0000  ' }),
    service.complete('user-a', 'node-a', created.id, { claimToken: claim.claimToken, error: 'conflicting write' }),
  ]);
  assert.equal(attempts.filter((attempt) => attempt.status === 'fulfilled').length, 1);
  assert.equal(attempts.filter((attempt) => attempt.status === 'rejected').length, 1);
  const row = repository.rows[0];
  assert.ok(row.status === FOUNDER_REMOTE_COMPLETE || row.status === FOUNDER_REMOTE_FAILED);
  assert.equal(Boolean(row.result) && Boolean(row.error), false);
  await expectCode(service.complete('user-a', 'node-a', created.id, { claimToken: claim.claimToken, result: 'replay' }), 'INVALID_CLAIM');

  const unclaimed = await service.create('user-a', 'session-a', 'other', 'node-a');
  await expectCode(service.complete('user-a', 'node-a', unclaimed.id, { claimToken: claim.claimToken, result: 'no claim' }), 'INVALID_CLAIM');

  const expired = harness();
  const expiring = await expired.service.create('user-a', 'session-a', 'expires', 'node-a');
  const expiringClaim = await expired.service.claim('user-a', 'node-a', expiring.id, claimBody);
  expired.advance(FOUNDER_REMOTE_CLAIM_TTL_MS + 1);
  await expectCode(
    expired.service.complete('user-a', 'node-a', expiring.id, { claimToken: expiringClaim.claimToken, result: 'late' }),
    'INVALID_CLAIM',
  );
});

test('completion preserves result/error separately and status never claims delivery', async () => {
  const { repository, service } = harness();
  const success = await service.create('user-a', 'session-a', 'remote success', 'node-a');
  const successClaim = await service.claim('user-a', 'node-a', success.id, claimBody);
  const successStatus = await service.complete('user-a', 'node-a', success.id, {
    claimToken: successClaim.claimToken,
    result: 'built successfully',
  });
  assert.equal(successStatus.status, FOUNDER_REMOTE_COMPLETE);
  assert.equal(successStatus.executionStatus, 'complete');
  assert.equal(successStatus.result, 'built successfully');
  assert.equal(successStatus.error, null);
  assert.equal(successStatus.delivered, false);
  assert.equal(successStatus.failed, false);

  const failure = await service.create('user-a', 'session-a', 'remote failure', 'node-a');
  const failureClaim = await service.claim('user-a', 'node-a', failure.id, claimBody);
  const failureStatus = await service.complete('user-a', 'node-a', failure.id, {
    claimToken: failureClaim.claimToken,
    error: 'builder failed',
  });
  assert.equal(failureStatus.status, FOUNDER_REMOTE_FAILED);
  assert.equal(failureStatus.result, null);
  assert.equal(failureStatus.error, 'builder failed');
  assert.equal(failureStatus.delivered, false);
  assert.equal(failureStatus.failed, true);
  assert.equal(repository.rows[1].result, null);
});

test('envelope mutation blocks completion while a terminal error is allowed for an intact claimed row', async () => {
  const mutated = harness();
  const created = await mutated.service.create('user-a', 'session-a', 'remote', 'node-a');
  const claim = await mutated.service.claim('user-a', 'node-a', created.id, claimBody);
  mutated.repository.rows[0].prompt = 'changed after claim';
  await expectCode(
    mutated.service.complete('user-a', 'node-a', created.id, { claimToken: claim.claimToken, error: 'changed envelope' }),
    'INVALID_CLAIM',
  );

  const intact = harness();
  const intactCreated = await intact.service.create('user-a', 'session-a', 'remote', 'node-a');
  const intactClaim = await intact.service.claim('user-a', 'node-a', intactCreated.id, claimBody);
  const status = await intact.service.complete('user-a', 'node-a', intactCreated.id, {
    claimToken: intactClaim.claimToken,
    error: 'Remote request changed before local approval.',
  });
  assert.equal(status.status, FOUNDER_REMOTE_FAILED);
  assert.equal(status.error, 'Remote request changed before local approval.');
});

test('raw claim token never leaks through pending, status, stored rows, or rejection messages', async () => {
  const { repository, service } = harness();
  const created = await service.create('user-a', 'session-a', 'remote', 'node-a');
  const claim = await service.claim('user-a', 'node-a', created.id, claimBody);
  const status = await service.status('user-a', created.id);
  const pending = await service.pending('user-a', 'node-a');
  const wrongToken = `Z${claim.claimToken.slice(1)}`;
  let rejection = '';
  try {
    await service.complete('user-a', 'node-a', created.id, { claimToken: wrongToken, result: 'no' });
  } catch (error) {
    rejection = error instanceof Error ? error.message : String(error);
  }

  assert.equal(JSON.stringify(status).includes(claim.claimToken), false);
  assert.equal(JSON.stringify(status).includes(repository.rows[0].claimTokenHash!), false);
  assert.equal(JSON.stringify(pending).includes(claim.claimToken), false);
  assert.equal(JSON.stringify(repository.rows).includes(claim.claimToken), false);
  assert.equal(rejection.includes(wrongToken), false);
  assert.equal(rejection.includes(claim.claimToken), false);
});

test('owner cancellation is pending-safe and claim-token-bound while running', async () => {
  const pending = harness();
  const pendingCreated = await pending.service.create('user-a', 'session-a', 'pending', 'node-a');
  const pendingStatus = await pending.service.cancel('user-a', pendingCreated.id, 'user stopped');
  assert.equal(pendingStatus.status, FOUNDER_REMOTE_FAILED);
  assert.match(pendingStatus.error!, /before the desktop claimed/);

  const running = harness();
  const created = await running.service.create('user-a', 'session-a', 'running', 'node-a');
  const claim = await running.service.claim('user-a', 'node-a', created.id, claimBody);
  const requested = await running.service.cancel('user-a', created.id, 'stop now');
  assert.equal(requested.executionStatus, 'cancellation_requested');
  assert.equal(requested.cancellationRequested, true);
  assert.equal(requested.cancellationReason, 'stop now');
  await expectCode(
    running.service.cancelStatus('user-a', 'node-a', created.id, { claimToken: `Z${claim.claimToken.slice(1)}` }),
    'INVALID_CLAIM',
  );
  const cancelStatus = await running.service.cancelStatus('user-a', 'node-a', created.id, { claimToken: claim.claimToken });
  assert.deepEqual(cancelStatus, { id: created.id, cancellationRequested: true, reason: 'stop now' });
  const terminal = await running.service.complete('user-a', 'node-a', created.id, { claimToken: claim.claimToken, error: 'AbortError' });
  assert.equal(terminal.status, FOUNDER_REMOTE_FAILED);
  assert.match(terminal.error!, /Cancelled by the Founder owner/);
});
