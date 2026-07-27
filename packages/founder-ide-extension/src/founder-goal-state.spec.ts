import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  createFounderGoalAmendmentDecision,
  enqueueFounderDecision,
  initialFounderGoalState,
  normalizeFounderGoalState,
  pendingFounderGoalDecisions,
  resolveFounderGoalUiDecision,
  updateFounderGoalObjective,
  type FounderGoalUiDecision,
} from './founder-goal-state';

const now = new Date('2026-07-27T00:00:00.000Z');

const decision: FounderGoalUiDecision = {
  id: 'decision-housekeeping',
  kind: 'housekeeping',
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
  evidence: ['2 generated directories, 1.4 GB total'],
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

  it('reviews a goal amendment before changing the active goal', () => {
    const state = initialFounderGoalState('Founder IDE', now);
    const amendment = createFounderGoalAmendmentDecision(
      state,
      'Ship Founder IDE with verified goal control',
      now,
    );
    const queued = enqueueFounderDecision(state, amendment);
    assert.equal(queued.objective, state.objective);
    const resolved = resolveFounderGoalUiDecision(queued, {
      decisionId: amendment.id,
      selectedOptionId: 'apply',
      now: new Date('2026-07-27T03:00:00.000Z'),
    });
    assert.equal(
      resolved.objective,
      'Ship Founder IDE with verified goal control',
    );
    assert.equal(resolved.version, 2);
  });

  it('can reject a proposed goal without disturbing the active goal', () => {
    const state = initialFounderGoalState('Founder IDE', now);
    const amendment = createFounderGoalAmendmentDecision(
      state,
      'Replace the product with an unrelated experiment',
      now,
    );
    const resolved = resolveFounderGoalUiDecision(
      enqueueFounderDecision(state, amendment),
      {
        decisionId: amendment.id,
        selectedOptionId: 'keep',
        now,
      },
    );
    assert.equal(resolved.objective, state.objective);
    assert.equal(resolved.version, 1);
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
