import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  canCompleteFounderGoal,
  createHousekeepingDecision,
  resolveFounderDecision,
  taskCanContinue,
  validateFounderDecisionRequest,
  type FounderDecisionRequest,
  type FounderGoalContract,
} from '../founder-goal-control';

const goal: FounderGoalContract = {
  id: 'goal-v1',
  version: 3,
  objective: 'Ship Founder OS V1 with clean-machine proof.',
  constraints: ['One editing owner', 'No unapproved deletion'],
  successEvidence: [
    { id: 'tests', label: 'Automated tests pass', kind: 'test', required: true },
    { id: 'visual', label: 'Installed UI reviewed', kind: 'visual', required: true },
    { id: 'optional', label: 'Founder note', kind: 'human', required: false },
  ],
  status: 'active',
  updatedAt: '2026-07-27T00:00:00.000Z',
};

function researchDecision(): FounderDecisionRequest {
  return {
    id: 'decision-research',
    goalId: goal.id,
    goalVersion: goal.version,
    kind: 'research_preference',
    risk: 'read_only',
    title: 'Choose research depth',
    question: 'How much competitor research should Founder perform?',
    options: [
      {
        id: 'bounded',
        label: 'Bounded review',
        description: 'Review three primary sources.',
        impact: 'Keeps the task within its token budget.',
        recommended: true,
      },
      {
        id: 'deep',
        label: 'Deep review',
        description: 'Review up to ten primary sources.',
        impact: 'Uses more time and tokens.',
      },
    ],
    allowCustomAnswer: true,
    blockingTaskIds: ['research-task'],
    independentWorkMayContinue: true,
    evidence: [],
    createdAt: '2026-07-27T00:00:00.000Z',
    status: 'pending',
    autoResolveOptionId: 'bounded',
  };
}

describe('Founder goal control', () => {
  it('blocks only tasks that depend on a pending decision', () => {
    const decision = researchDecision();
    assert.equal(taskCanContinue('research-task', [decision]), false);
    assert.equal(taskCanContinue('build-unrelated-ui', [decision]), true);
  });

  it('allows policy resolution only for read-only research preferences', () => {
    const decision = researchDecision();
    const resolution = resolveFounderDecision(decision, {
      selectedOptionId: 'bounded',
      resolvedBy: 'approved_policy',
      now: new Date('2026-07-27T01:00:00.000Z'),
    });
    assert.equal(resolution.resolvedBy, 'approved_policy');

    const deletion = {
      ...decision,
      kind: 'housekeeping' as const,
      risk: 'destructive' as const,
    };
    assert.throws(
      () => resolveFounderDecision(deletion, {
        selectedOptionId: 'bounded',
        resolvedBy: 'approved_policy',
      }),
      /requires the founder/,
    );
  });

  it('requires every mandatory evidence item before completion', () => {
    assert.equal(canCompleteFounderGoal(goal, ['tests']), false);
    assert.equal(canCompleteFounderGoal(goal, ['tests', 'visual']), true);
  });

  it('turns housekeeping research into an explicit founder decision', () => {
    const request = createHousekeepingDecision({
      id: 'housekeeping-1',
      goal,
      createdAt: new Date('2026-07-27T02:00:00.000Z'),
      candidates: [
        {
          id: 'cache-a',
          path: 'node_modules/.cache',
          sizeBytes: 1_610_612_736,
          category: 'cache',
          evidence: ['Generated cache; clean install recreates it.'],
          referencedBy: [],
          recommendedAction: 'delete',
          reversible: true,
        },
        {
          id: 'source-a',
          path: 'packages/founder-ide-extension/src/extension.ts',
          sizeBytes: 80_000,
          category: 'obsolete_source',
          evidence: ['Still referenced by the extension manifest.'],
          referencedBy: ['packages/founder-ide-extension/package.json'],
          recommendedAction: 'keep',
          reversible: false,
        },
      ],
    });

    assert.equal(request.kind, 'housekeeping');
    assert.equal(request.status, 'pending');
    assert.equal(request.independentWorkMayContinue, true);
    assert.match(request.question, /1 proposed deletion/);
    assert.equal(validateFounderDecisionRequest(request).length, 0);
    assert.equal(request.autoResolveOptionId, undefined);
    assert.equal(request.options[0].id, 'approve_selected');
    assert.throws(
      () => resolveFounderDecision(request, {
        selectedOptionId: 'approve_selected',
        selectedCandidateIds: [],
      }),
      /Select at least one/,
    );
    assert.deepEqual(
      resolveFounderDecision(request, {
        selectedOptionId: 'approve_selected',
        selectedCandidateIds: ['cache-a', 'source-a', 'unknown'],
      }).selectedCandidateIds,
      ['cache-a'],
    );
    assert.equal(
      resolveFounderDecision(request, {
        selectedOptionId: 'keep_all',
        selectedCandidateIds: ['cache-a'],
      }).selectedCandidateIds,
      undefined,
    );
  });

  it('rejects malformed decision choices and unsafe auto-resolution', () => {
    const invalid = {
      ...researchDecision(),
      kind: 'permission' as const,
      risk: 'external_write' as const,
      autoResolveOptionId: 'bounded',
    };
    assert.deepEqual(
      validateFounderDecisionRequest(invalid),
      ['This decision kind or risk cannot be auto-resolved.'],
    );
  });
});
