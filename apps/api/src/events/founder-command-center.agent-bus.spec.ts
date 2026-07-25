import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { AgentBusHandoff, AgentBusLedgerEventType } from '@dcf/utils';
import { FounderCommandCenterService } from './founder-command-center.service';

function ledgerPayload(
  handoffId: string,
  type: AgentBusLedgerEventType,
  offset: number,
) {
  return {
    agentBusLedger: {
      eventId: `${handoffId}:${type.toLowerCase()}`,
      handoffId,
      type,
      at: new Date(1_000 + offset).toISOString(),
      actor: 'test-owner',
    },
  };
}

describe('FounderCommandCenter AgentBus v2 integration', () => {
  it('releases a persisted dependency once and records an idempotent lifecycle', async () => {
    const rows = [
      { payload: ledgerPayload('prior-build', 'CREATED', 1) },
      { payload: ledgerPayload('prior-build', 'CLAIMED', 2) },
      { payload: ledgerPayload('prior-build', 'STARTED', 3) },
      { payload: ledgerPayload('prior-build', 'VERIFYING', 4) },
      { payload: ledgerPayload('prior-build', 'COMPLETED', 5) },
    ];
    const created: Array<{ data: { payload: unknown } }> = [];
    const notifications: Array<{ title: string }> = [];
    const prisma = {
      founderEvent: {
        findMany: async () => rows,
        create: async (input: { data: { payload: unknown } }) => {
          created.push(input);
          rows.push({ payload: input.data.payload as ReturnType<typeof ledgerPayload> });
          return { id: `event-${created.length}` };
        },
      },
    };
    const notificationService = {
      notifyUser: async (_userId: string, input: { title: string }) => {
        notifications.push(input);
      },
    };
    const service = new FounderCommandCenterService(
      prisma as never,
      {} as never,
      {} as never,
      notificationService as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
    );
    const handoff: AgentBusHandoff = {
      version: 2,
      id: 'publish-result',
      from: 'builder',
      to: 'founder_queue',
      kind: 'BUILD_COMPLETED',
      title: 'Build verified',
      detail: 'The owned scope passed its checks.',
      dependsOn: ['prior-build'],
      priorAttempts: 0,
      stallThreshold: 2,
      payload: { artifactPath: 'artifacts/build-receipt.json' },
    };

    const first = await service.applyAgentBusHandoffs(
      'user-1',
      [handoff],
      'founder-1',
      'project-1',
    );
    assert.equal(first.applied, 1);
    assert.deepEqual(first.handoffIds, ['publish-result']);
    assert.equal(notifications.length, 1);
    assert.deepEqual(
      created.map(({ data }) => (
        (data.payload as ReturnType<typeof ledgerPayload>).agentBusLedger.type
      )),
      ['CREATED', 'CLAIMED', 'STARTED', 'VERIFYING', 'COMPLETED'],
    );

    const second = await service.applyAgentBusHandoffs(
      'user-1',
      [handoff],
      'founder-1',
      'project-1',
    );
    assert.equal(second.applied, 0);
    assert.equal(second.skipped, 1);
    assert.equal(created.length, 5);
    assert.equal(notifications.length, 1);
  });
});
