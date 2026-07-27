import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { renderFounderGoalContext } from './founder-goal-context';
import {
  createFounderGoalAmendmentDecision,
  enqueueFounderDecision,
  initialFounderGoalState,
  resolveFounderGoalUiDecision,
} from './founder-goal-state';

describe('Founder Goal prompt context', () => {
  it('keeps one editable goal as the explicit North Star', () => {
    const state = initialFounderGoalState(
      'Payments',
      new Date('2026-07-27T00:00:00Z'),
    );

    const context = renderFounderGoalContext(state);

    assert.match(context, /Pursuing Goal \(North Star\)/);
    assert.match(context, /Objective: Build and ship Payments/);
    assert.match(context, /Do not silently replace, expand, or declare this goal complete/);
    assert.match(context, /Report evidence against both the current request and this goal/);
  });

  it('shows pending decisions but never treats them as permission', () => {
    const base = initialFounderGoalState(
      'Payments',
      new Date('2026-07-27T00:00:00Z'),
    );
    const decision = {
      ...createFounderGoalAmendmentDecision(
        base,
        'Ship Payments with verified checkout',
        new Date('2026-07-27T00:01:00Z'),
      ),
      blockingTaskIds: ['deploy-payments'],
    };
    const pending = enqueueFounderDecision(base, decision);

    const context = renderFounderGoalContext(pending);

    assert.match(context, /Pending founder decisions \(1 shown\)/);
    assert.match(context, /Review goal change/);
    assert.match(context, /Blocked task ids: deploy-payments/);
    assert.match(context, /silence, timeout, or a recommended option never grants permission/);
  });

  it('omits resolved decisions and remains bounded', () => {
    const base = initialFounderGoalState(
      'x'.repeat(1_000),
      new Date('2026-07-27T00:00:00Z'),
    );
    const decision = createFounderGoalAmendmentDecision(
      base,
      'Ship the bounded result',
      new Date('2026-07-27T00:01:00Z'),
    );
    const resolved = resolveFounderGoalUiDecision(
      enqueueFounderDecision(base, decision),
      {
        decisionId: decision.id,
        selectedOptionId: 'keep',
        now: new Date('2026-07-27T00:02:00Z'),
      },
    );

    const context = renderFounderGoalContext(resolved);

    assert.doesNotMatch(context, /Pending founder decisions/);
    assert.ok(context.length <= 2_400);
  });
});
