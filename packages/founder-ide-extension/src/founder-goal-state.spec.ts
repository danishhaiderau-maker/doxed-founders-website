import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  enqueueFounderDecision,
  initialFounderGoalState,
  normalizeFounderGoalState,
  pendingFounderGoalDecisions,
  resolveFounderGoalUiDecision,
  updateFounderGoalObjective,
  type FounderGoalUiDecision,
} from './founder-goal-state';

const decision: FounderGoalUiDecision = {
  id: 'decision-housekeeping',
  title: 'Review housekeeping',
  question: 'What should Founder remove?',
  options: [
    {
      id: 'review',
      label: 'Review each',
      description: 'Inspect every candidate before deletion.',
      recommended: true,
    },
    {
      id: 'keep',
      label: 'Keep everything',
      description: 'Cancel this cleanup batch.',
    },
  ],
  allowCustomAnswer: true,
  independentWorkMayContinue: true,
  risk: 'destructive',
  status: 'pending',
  createdAt: '2026-07-27T00:00:00.000Z',
};

describe('Founder IDE goal state', () => {
  it('creates one readable project goal and versions real edits', () => {
    const state = initialFounderGoalState(
      'Founder IDE',
      new Date('2026-07-27T00:00:00.000Z'),
    );
    assert.equal(state.objective, 'Build and ship Founder IDE');
    const updated = updateFounderGoalObjective(
      state,
      '  Ship   Founder OS V1  ',
      new Date('2026-07-27T01:00:00.000Z'),
    );
    assert.equal(updated.objective, 'Ship Founder OS V1');
    assert.equal(updated.version, 2);
  });

  it('normalizes malformed persisted state without trusting it', () => {
    const state = normalizeFounderGoalState({
      id: '',
      version: -2,
      objective: '  Keep   the goal readable ',
      status: 'unknown',
      updatedAt: 'not-a-date',
      decisions: [{ id: 'broken' }],
    }, 'Workspace', new Date('2026-07-27T02:00:00.000Z'));
    assert.equal(state.objective, 'Keep the goal readable');
    assert.equal(state.version, 1);
    assert.equal(state.status, 'active');
    assert.deepEqual(state.decisions, []);
  });

  it('queues a decision idempotently and records a founder answer', () => {
    const state = enqueueFounderDecision(
      initialFounderGoalState('Workspace'),
      decision,
    );
    assert.equal(enqueueFounderDecision(state, decision), state);
    assert.equal(pendingFounderGoalDecisions(state).length, 1);
    const resolved = resolveFounderGoalUiDecision(state, {
      decisionId: decision.id,
      selectedOptionId: 'review',
      now: new Date('2026-07-27T03:00:00.000Z'),
    });
    assert.equal(pendingFounderGoalDecisions(resolved).length, 0);
    assert.equal(resolved.decisions[0]?.selectedOptionId, 'review');
  });

  it('rejects an unknown option and disallowed custom answer', () => {
    const state = enqueueFounderDecision(
      initialFounderGoalState('Workspace'),
      { ...decision, allowCustomAnswer: false },
    );
    assert.throws(
      () => resolveFounderGoalUiDecision(state, {
        decisionId: decision.id,
        selectedOptionId: 'delete_all',
      }),
      /option is invalid/,
    );
    assert.throws(
      () => resolveFounderGoalUiDecision(state, {
        decisionId: decision.id,
        customAnswer: 'Delete it anyway',
      }),
      /does not accept a custom answer/,
    );
  });
});
