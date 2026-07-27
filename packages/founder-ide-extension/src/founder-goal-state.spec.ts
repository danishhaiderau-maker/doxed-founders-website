import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  attachFounderDecisionResearch,
  createFounderGoalAmendmentDecision,
  createFounderHousekeepingDecision,
  enqueueFounderDecision,
  founderGoalUiTaskCanContinue,
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
  blockingTaskIds: ['cleanup-task'],
  researchFindings: [],
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
      [
        {
          id: 'builder-task',
          title: 'Build goal controls',
          status: 'working',
          files: ['src/goal.ts'],
        },
        {
          id: 'completed-task',
          title: 'Finish navigation',
          status: 'complete',
          files: ['src/nav.ts'],
        },
      ],
    );
    const queued = enqueueFounderDecision(state, amendment);
    assert.equal(queued.objective, state.objective);
    assert.deepEqual(amendment.blockingTaskIds, ['builder-task']);
    assert.match(amendment.evidence.join('\n'), /Affected tasks: Build goal controls/);
    assert.match(amendment.evidence.join('\n'), /Budget impact: recalculate/);
    assert.equal(founderGoalUiTaskCanContinue(queued, 'builder-task'), false);
    assert.equal(founderGoalUiTaskCanContinue(queued, 'unrelated-task'), true);
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

  it('defers a goal amendment without resolving it or changing the goal', () => {
    const state = initialFounderGoalState('Founder IDE', now);
    const amendment = createFounderGoalAmendmentDecision(
      state,
      'Ship a revised Founder IDE',
      now,
      [{
        id: 'active-task',
        title: 'Build Founder IDE',
        status: 'verifying',
        files: [],
      }],
    );
    const queued = enqueueFounderDecision(state, amendment);
    const deferred = resolveFounderGoalUiDecision(queued, {
      decisionId: amendment.id,
      selectedOptionId: 'defer',
      now: new Date('2026-07-27T03:30:00.000Z'),
    });
    assert.equal(deferred, queued);
    assert.equal(pendingFounderGoalDecisions(deferred).length, 1);
    assert.equal(deferred.objective, state.objective);
    assert.equal(founderGoalUiTaskCanContinue(deferred, 'active-task'), false);
    assert.equal(founderGoalUiTaskCanContinue(deferred, 'other-task'), true);
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

  it('keeps housekeeping read-only until selected candidates are approved', () => {
    const state = initialFounderGoalState('Founder IDE', now);
    const housekeeping = createFounderHousekeepingDecision(
      state,
      [
        {
          id: 'cache-output',
          path: 'packages/founder-ide-extension/out',
          sizeBytes: 125_000_000,
          category: 'generated',
          evidence: ['Rebuilt by the extension compiler.'],
          recommendedAction: 'delete',
          reversible: true,
        },
        {
          id: 'source-file',
          path: 'packages/founder-ide-extension/src/extension.ts',
          sizeBytes: 10_000,
          category: 'obsolete_source',
          evidence: ['Still imported by the extension entry point.'],
          recommendedAction: 'keep',
          reversible: false,
        },
      ],
      now,
    );
    assert.equal(housekeeping.status, 'pending');
    assert.equal(housekeeping.risk, 'reversible_write');
    assert.match(housekeeping.question, /119\.2 MB/);
    const queued = enqueueFounderDecision(state, housekeeping);
    assert.throws(
      () => resolveFounderGoalUiDecision(queued, {
        decisionId: housekeeping.id,
        selectedOptionId: 'approve_selected',
        selectedCandidateIds: [],
        now,
      }),
      /Select at least one/,
    );
    const resolved = resolveFounderGoalUiDecision(queued, {
      decisionId: housekeeping.id,
      selectedOptionId: 'approve_selected',
      selectedCandidateIds: ['cache-output', 'source-file', 'unknown'],
      now,
    });
    assert.deepEqual(
      resolved.decisions[0]?.selectedCandidateIds,
      ['cache-output'],
    );
    assert.equal(resolved.objective, state.objective);
    const rejected = resolveFounderGoalUiDecision(queued, {
      decisionId: housekeeping.id,
      selectedOptionId: 'keep_all',
      selectedCandidateIds: ['cache-output'],
      now,
    });
    assert.equal(rejected.decisions[0]?.selectedCandidateIds, undefined);
  });

  it('keeps housekeeping pending for more research or revised founder instructions', () => {
    const state = initialFounderGoalState('Founder IDE', now);
    const housekeeping = createFounderHousekeepingDecision(
      state,
      [{
        id: 'cache-output',
        path: 'packages/founder-ide-extension/out',
        sizeBytes: 125_000_000,
        category: 'generated',
        evidence: ['Rebuilt by the extension compiler.'],
        recommendedAction: 'delete',
        reversible: true,
      }],
      now,
    );
    const queued = enqueueFounderDecision(state, housekeeping);
    const researching = resolveFounderGoalUiDecision(queued, {
      decisionId: housekeeping.id,
      selectedOptionId: 'research_more',
      now,
    });
    assert.equal(researching, queued);
    assert.equal(pendingFounderGoalDecisions(researching).length, 1);

    const revised = resolveFounderGoalUiDecision(queued, {
      decisionId: housekeeping.id,
      customAnswer:
        'Exclude every release artifact and inspect only reproducible caches.',
      now: new Date('2026-07-27T04:00:00.000Z'),
    });
    assert.equal(pendingFounderGoalDecisions(revised).length, 1);
    assert.equal(revised.decisions[0]?.researchFindings.length, 1);
    assert.equal(
      revised.decisions[0]?.researchFindings[0]?.summary,
      'Exclude every release artifact and inspect only reproducible caches.',
    );
    assert.equal(revised.decisions[0]?.selectedCandidateIds, undefined);
  });

  it('attaches bounded research without resolving permission or blocking unrelated work', () => {
    const state = enqueueFounderDecision(
      initialFounderGoalState('Founder IDE', now),
      decision,
    );
    const researched = attachFounderDecisionResearch(state, {
      decisionId: decision.id,
      finding: {
        id: 'finding-1',
        title: 'Cache evidence',
        summary: 'The generated output is reproducible from committed source.',
        sources: ['packages/founder-ide-extension/tsconfig.json'],
        createdAt: now.toISOString(),
      },
      now,
    });
    assert.equal(researched.decisions[0]?.status, 'pending');
    assert.equal(researched.decisions[0]?.researchFindings.length, 1);
    assert.equal(founderGoalUiTaskCanContinue(researched, 'cleanup-task'), false);
    assert.equal(founderGoalUiTaskCanContinue(researched, 'research-more'), true);
    assert.equal(
      attachFounderDecisionResearch(researched, {
        decisionId: decision.id,
        finding: researched.decisions[0]?.researchFindings[0],
        now,
      }),
      researched,
    );
    assert.throws(
      () => attachFounderDecisionResearch(researched, {
        decisionId: decision.id,
        finding: {
          id: 'secret',
          title: 'Unsafe',
          summary: 'api_key=do-not-store-this',
          sources: [],
          createdAt: now.toISOString(),
        },
      }),
      /invalid/,
    );
    const resolved = resolveFounderGoalUiDecision(researched, {
      decisionId: decision.id,
      selectedOptionId: 'review',
      now,
    });
    assert.equal(founderGoalUiTaskCanContinue(resolved, 'cleanup-task'), true);
    assert.throws(
      () => attachFounderDecisionResearch(resolved, {
        decisionId: decision.id,
        finding: {
          id: 'too-late',
          title: 'Late research',
          summary: 'This must not rewrite a resolved decision.',
          sources: [],
          createdAt: now.toISOString(),
        },
      }),
      /not pending/,
    );
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
