import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildGoalContractJsonFile,
  buildTasksJsonFile,
  parseGoalContractJson,
} from '../founder-os-memory-files';
import type {
  FounderDecisionRequest,
  FounderGoalContract,
} from '../founder-goal-control';

const goal: FounderGoalContract = {
  id: 'founder-v1',
  version: 4,
  objective: 'Ship Founder OS V1 with clean-machine evidence.',
  constraints: ['No deletion without founder approval'],
  successEvidence: [
    {
      id: 'installed',
      label: 'Installed application passes visual QA',
      kind: 'visual',
      required: true,
    },
  ],
  status: 'active',
  updatedAt: '2026-07-28T01:00:00.000Z',
};

function decision(
  input: Partial<FounderDecisionRequest> & Pick<FounderDecisionRequest, 'id' | 'kind'>,
): FounderDecisionRequest {
  const { id, kind, ...overrides } = input;
  return {
    id,
    goalId: goal.id,
    goalVersion: goal.version,
    kind,
    risk: kind === 'housekeeping' ? 'destructive' : 'read_only',
    title: overrides.title ?? 'Choose the product direction',
    question: overrides.question ?? 'Which direction should Founder use?',
    options: [
      {
        id: 'recommended',
        label: 'Use the recommended path',
        description: 'Keep the product focused.',
        impact: 'The coding agents share one durable direction.',
        recommended: true,
      },
      {
        id: 'alternative',
        label: 'Use the alternative',
        description: 'Use a different path.',
        impact: 'The goal contract changes.',
      },
    ],
    allowCustomAnswer: true,
    blockingTaskIds: [],
    independentWorkMayContinue: true,
    evidence: [],
    createdAt: '2026-07-28T01:05:00.000Z',
    status: overrides.status ?? 'resolved',
    ...overrides,
  };
}

describe('Founder OS repository memory', () => {
  it('builds deterministic tasks without using the wall clock', () => {
    const input = {
      currentGoal: goal.objective,
      updatedAt: '2026-07-28T02:00:00.000Z',
      tasks: [
        {
          id: 'task-1',
          title: 'Finish installed QA',
          status: 'ACTIVE',
          kind: 'TASK',
          done: false,
        },
      ],
    };

    assert.deepEqual(buildTasksJsonFile(input), buildTasksJsonFile(input));
    assert.equal(
      buildTasksJsonFile(input).updatedAt,
      '2026-07-28T02:00:00.000Z',
    );
  });

  it('commits only the newest goal and resolved product decisions', () => {
    const productDecision = decision({
      id: 'decision-product',
      kind: 'research_preference',
    });
    const privateHousekeeping = decision({
      id: 'decision-housekeeping',
      kind: 'housekeeping',
      title: 'Delete local build caches',
    });
    const pendingDecision = decision({
      id: 'decision-pending',
      kind: 'goal_amendment',
      status: 'pending',
    });
    const memoryGraph = {
      _founderGoalControl: {
        schemaVersion: 1,
        workspaces: {
          older: {
            goal: { ...goal, version: 3, objective: 'Old objective' },
            decisions: [],
            resolutions: [],
            updatedAt: '2026-07-27T00:00:00.000Z',
          },
          current: {
            goal,
            decisions: [
              productDecision,
              privateHousekeeping,
              pendingDecision,
            ],
            resolutions: [
              {
                requestId: productDecision.id,
                selectedOptionId: 'recommended',
                customAnswer: 'Private detail must not enter Git.',
                resolvedAt: '2026-07-28T01:30:00.000Z',
                resolvedBy: 'founder',
              },
              {
                requestId: privateHousekeeping.id,
                selectedCandidateIds: ['local-cache'],
                resolvedAt: '2026-07-28T01:35:00.000Z',
                resolvedBy: 'founder',
              },
            ],
            updatedAt: '2026-07-28T01:40:00.000Z',
          },
        },
      },
    };

    const contract = buildGoalContractJsonFile({
      memoryGraph,
      fallbackGoal: 'Fallback objective',
      updatedAt: '2026-07-28T01:20:00.000Z',
    });

    assert.equal(contract.goal?.version, 4);
    assert.equal(contract.currentGoal, goal.objective);
    assert.equal(contract.updatedAt, '2026-07-28T01:40:00.000Z');
    assert.deepEqual(contract.durableDecisions, [
      {
        id: productDecision.id,
        kind: 'research_preference',
        title: productDecision.title,
        outcome: 'Use the recommended path',
        resolvedAt: '2026-07-28T01:30:00.000Z',
      },
    ]);
    assert.doesNotMatch(JSON.stringify(contract), /Private detail|local-cache/);
  });

  it('round-trips a valid goal contract and rejects unrelated JSON', () => {
    const contract = buildGoalContractJsonFile({
      memoryGraph: null,
      fallbackGoal: 'Define the first milestone',
      updatedAt: '2026-07-28T02:00:00.000Z',
    });

    assert.deepEqual(
      parseGoalContractJson(JSON.stringify(contract)),
      contract,
    );
    assert.equal(parseGoalContractJson('{"version":2}'), null);
  });

  it('redacts secret-like goal text before it reaches repository memory', () => {
    const contract = buildGoalContractJsonFile({
      memoryGraph: {
        _founderGoalControl: {
          goal: {
            ...goal,
            objective: 'Use api_key=super-secret-value-12345 to ship.',
            constraints: ['Bearer abcdefghijklmnopqrstuvwxyz'],
          },
          decisions: [],
          resolutions: [],
          updatedAt: goal.updatedAt,
        },
      },
      fallbackGoal: 'Fallback objective',
      updatedAt: goal.updatedAt,
    });

    assert.equal(contract.currentGoal, '[redacted from repository]');
    assert.equal(contract.goal?.constraints[0], '[redacted from repository]');
    assert.doesNotMatch(
      JSON.stringify(contract),
      /super-secret-value|abcdefghijklmnopqrstuvwxyz/,
    );
  });
});
