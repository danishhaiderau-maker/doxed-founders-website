import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type {
  AgentBusHandoff,
  AgentBusLedgerEventType,
  FounderDecisionRequest,
  FounderGoalContract,
} from '@dcf/utils';
import { FounderAgentRunService } from '../founder-agent-run/founder-agent-run.service';
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
      { getBlockingDecisionIds: async () => [] } as never,
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

  it('blocks only the handoff named by a pending founder decision', async () => {
    const rows: Array<{ payload: ReturnType<typeof ledgerPayload> }> = [];
    const quickBuilds: string[] = [];
    const notifications: string[] = [];
    const prisma = {
      founderEvent: {
        findMany: async () => rows,
        create: async (input: { data: { payload: unknown } }) => {
          rows.push({
            payload: input.data.payload as ReturnType<typeof ledgerPayload>,
          });
          return { id: `event-${rows.length}` };
        },
      },
    };
    const service = new FounderCommandCenterService(
      prisma as never,
      {} as never,
      {} as never,
      {
        notifyUser: async (_userId: string, input: { title: string }) => {
          notifications.push(input.title);
        },
      } as never,
      {
        quickBuild: async (_userId: string, input: { prompt: string }) => {
          quickBuilds.push(input.prompt);
        },
      } as never,
      {} as never,
      {} as never,
      {} as never,
      {
        getBlockingDecisionIds: async (_userId: string, taskId: string) =>
          taskId === 'blocked-build' ? ['decision-1'] : [],
      } as never,
    );
    const handoffs: AgentBusHandoff[] = [
      {
        version: 2,
        id: 'blocked-build',
        from: 'research',
        to: 'builder',
        kind: 'RESEARCH_COMPLETED',
        title: 'Implement the reviewed option',
        detail: 'Wait for the founder choice.',
        payload: {
          artifactPath: 'artifacts/research.json',
          spec: 'Build only after the founder decides.',
        },
      },
      {
        version: 2,
        id: 'independent-research',
        from: 'research',
        to: 'founder_queue',
        kind: 'RESEARCH_COMPLETED',
        title: 'Independent research finished',
        detail: 'This work is unrelated to the pending choice.',
        payload: { artifactPath: 'artifacts/independent.json' },
      },
    ];

    const result = await service.applyAgentBusHandoffs(
      'user-1',
      handoffs,
      'founder-1',
      'project-1',
    );

    assert.equal(result.applied, 1);
    assert.deepEqual(result.handoffIds, ['independent-research']);
    assert.equal(result.skipped, 1);
    assert.deepEqual(quickBuilds, []);
    assert.deepEqual(notifications, ['Independent research finished']);
    const blockedEvents = rows
      .map((row) => row.payload.agentBusLedger)
      .filter((event) => event.handoffId === 'blocked-build');
    assert.deepEqual(
      blockedEvents.map((event) => event.type),
      ['CREATED', 'CLAIMED', 'BLOCKED'],
    );
  });

  it('refuses a build-queue launch while its exact task is awaiting a decision', async () => {
    let executed = false;
    const service = new FounderCommandCenterService(
      {
        founder: {
          findUnique: async () => ({ id: 'founder-1' }),
        },
        buildQueueItem: {
          findFirst: async () => ({
            id: 'task-1',
            founderId: 'founder-1',
            title: 'Delete reviewed cache files',
            spec: 'Delete only the exact approved paths.',
            cursorPrompt: null,
          }),
        },
      } as never,
      {} as never,
      {
        executeBuildTask: async () => {
          executed = true;
          throw new Error('A blocked build must not execute.');
        },
      } as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
      {
        getBlockingDecisionIds: async () => ['housekeeping-review-1'],
      } as never,
    );

    const result = await service.executeQueueAction('user-1', 'task-task-1');

    assert.equal(result.action, 'decision_required');
    assert.equal(executed, false);
    assert.deepEqual(result.blockingDecisionIds, ['housekeeping-review-1']);
  });
});

describe('FounderAgentRunService goal control', () => {
  it('persists decisions, keeps unrelated memory, and releases only after resolution', async () => {
    let memoryGraph: Record<string, unknown> = { keepMe: 'untouched' };
    const prisma = {
      founderBuilderSettings: {
        findUnique: async () => ({ memoryGraph }),
        upsert: async (input: {
          create: { memoryGraph: Record<string, unknown> };
          update: { memoryGraph: Record<string, unknown> };
        }) => {
          memoryGraph = structuredClone(input.update.memoryGraph);
          return { memoryGraph };
        },
      },
    };
    const service = new FounderAgentRunService(prisma as never);
    const goal: FounderGoalContract = {
      id: 'goal-v1',
      version: 1,
      objective: 'Ship Founder OS V1',
      constraints: ['Do not delete without approval.'],
      successEvidence: [
        { id: 'tests', label: 'Tests pass', kind: 'test', required: true },
      ],
      status: 'active',
      updatedAt: new Date(1_000).toISOString(),
    };
    const decision: FounderDecisionRequest = {
      id: 'decision-1',
      goalId: goal.id,
      goalVersion: goal.version,
      kind: 'research_preference',
      risk: 'read_only',
      title: 'Choose the code intelligence boundary',
      question: 'Which bounded context strategy should the agent use?',
      options: [
        {
          id: 'bounded',
          label: 'Bounded graph',
          description: 'Use graph-ranked context.',
          impact: 'Keeps the prompt below the repository-map budget.',
          recommended: true,
        },
        {
          id: 'whole-repo',
          label: 'Whole repository',
          description: 'Send a broad file inventory.',
          impact: 'Consumes more tokens.',
        },
      ],
      allowCustomAnswer: true,
      blockingTaskIds: ['task-a'],
      independentWorkMayContinue: true,
      evidence: ['Repo map benchmark is still running.'],
      createdAt: new Date(2_000).toISOString(),
      status: 'pending',
    };

    await service.saveGoal('user-1', goal);
    await service.queueDecision('user-1', decision);
    assert.equal(await service.taskCanContinue('user-1', 'task-a'), false);
    assert.equal(await service.taskCanContinue('user-1', 'task-b'), true);

    await service.appendDecisionResearch('user-1', decision.id, {
      id: 'finding-1',
      title: 'Bounded repo map result',
      summary: 'The map stayed below four thousand tokens.',
      sources: ['artifacts/repo-map-benchmark.json'],
      createdAt: new Date(3_000).toISOString(),
    });
    const pending = await service.getGoalControl('user-1');
    assert.equal(pending.decisions[0]?.status, 'pending');
    assert.equal(pending.decisions[0]?.researchFindings?.length, 1);
    assert.equal(memoryGraph.keepMe, 'untouched');

    const resolved = await service.resolveDecision('user-1', {
      requestId: decision.id,
      selectedOptionId: 'bounded',
    });
    assert.equal(resolved.decisions[0]?.status, 'resolved');
    assert.equal(resolved.resolutions[0]?.selectedOptionId, 'bounded');
    assert.equal(await service.taskCanContinue('user-1', 'task-a'), true);
  });

  it('drops corrupt stored entries and rejects secret-like research', async () => {
    let memoryGraph: Record<string, unknown> = {
      _founderGoalControl: {
        goal: null,
        decisions: [{ id: 'broken' }],
        resolutions: [{ requestId: 'broken' }],
        updatedAt: 'not-a-date',
      },
    };
    const prisma = {
      founderBuilderSettings: {
        findUnique: async () => ({ memoryGraph }),
        upsert: async (input: {
          update: { memoryGraph: Record<string, unknown> };
        }) => {
          memoryGraph = structuredClone(input.update.memoryGraph);
          return { memoryGraph };
        },
      },
    };
    const service = new FounderAgentRunService(prisma as never);
    const normalized = await service.getGoalControl('user-1');
    assert.deepEqual(normalized.decisions, []);
    assert.deepEqual(normalized.resolutions, []);

    const goal: FounderGoalContract = {
      id: 'goal-v1',
      version: 1,
      objective: 'Ship Founder OS V1',
      constraints: [],
      successEvidence: [
        { id: 'tests', label: 'Tests pass', kind: 'test', required: true },
      ],
      status: 'active',
      updatedAt: new Date().toISOString(),
    };
    await service.saveGoal('user-1', goal);
    await service.queueDecision('user-1', {
      id: 'decision-secret-test',
      goalId: goal.id,
      goalVersion: goal.version,
      kind: 'research_preference',
      risk: 'read_only',
      title: 'Research a safe option',
      question: 'Which safe option should be used?',
      options: [
        {
          id: 'one',
          label: 'One',
          description: 'First bounded option.',
          impact: 'No external change.',
        },
        {
          id: 'two',
          label: 'Two',
          description: 'Second bounded option.',
          impact: 'No external change.',
        },
      ],
      allowCustomAnswer: false,
      blockingTaskIds: [],
      independentWorkMayContinue: true,
      evidence: [],
      createdAt: new Date().toISOString(),
      status: 'pending',
    });
    await assert.rejects(
      service.appendDecisionResearch('user-1', 'decision-secret-test', {
        id: 'finding-secret',
        title: 'Credential check',
        summary: 'api_key=sk-this-must-never-be-stored',
        sources: [],
        createdAt: new Date().toISOString(),
      }),
      /secret-like/i,
    );
  });
});
