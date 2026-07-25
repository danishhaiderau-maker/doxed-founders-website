import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  AgentBusInvariantError,
  agentBusHandoffFingerprint,
  agentBusRetryDecision,
  agentBusScopeAllows,
  planAgentBusHandoffs,
  replayAgentBusEvents,
  resolveAgentBusGraph,
  validateAgentBusCompletion,
  type AgentBusCompletionValidation,
  type AgentBusHandoff,
  type AgentBusLedgerEvent,
} from '../agent-bus';

function handoff(
  id: string,
  overrides: Partial<AgentBusHandoff> = {},
): AgentBusHandoff {
  return {
    version: 2,
    id,
    from: 'founder_brain',
    to: 'builder',
    kind: 'RESEARCH_COMPLETED',
    title: id,
    detail: `Build ${id}`,
    payload: { scope: [`src/${id}/**`], budgetTokens: 10_000 },
    ...overrides,
  };
}

function failureReason(value: AgentBusCompletionValidation): string {
  return value.ok ? '' : value.reason;
}

describe('AgentBus v2 handoff planning', () => {
  it('creates deterministic retry-safe IDs and carries bounded contracts', () => {
    const input = {
      kind: 'RESEARCH_COMPLETED' as const,
      founderId: 'founder-1',
      title: 'Coordination gap',
      detail: 'Build a feature that prevents agents editing the same file.',
      researchSummary: 'Implement scoped leases and verification.',
      scope: ['packages/ide/**', '../unsafe/**'],
      budgetTokens: 12_000,
      budgetMs: 60_000,
      capabilityTags: ['TypeScript', ' security '],
      artifactPath: 'artifacts/coordination.json',
    };
    const first = planAgentBusHandoffs(input);
    const second = planAgentBusHandoffs(input);
    assert.equal(first[0]?.id, second[0]?.id);
    assert.notEqual(
      first[0]?.id,
      planAgentBusHandoffs({ ...input, scope: ['packages/api/**'] })[0]?.id,
    );
    assert.deepEqual(first[0]?.payload.scope, ['packages/ide/**']);
    assert.equal(first[0]?.payload.budgetTokens, 12_000);
    assert.equal(first[0]?.payload.budgetMs, 60_000);
    assert.deepEqual(first[0]?.payload.capabilityTags, ['security', 'typescript']);
    assert.equal(first[0]?.payload.artifactPath, 'artifacts/coordination.json');
  });

  it('includes scope and dependency policy in the dedupe fingerprint', () => {
    const base = handoff('build');
    assert.notEqual(
      agentBusHandoffFingerprint(base),
      agentBusHandoffFingerprint({
        ...base,
        payload: { ...base.payload, scope: ['src/other/**'] },
      }),
    );
  });
});

describe('AgentBus v2 dependency graph', () => {
  it('orders dependencies and redirects dependents to a superseding revision', () => {
    const research = handoff('research');
    const oldBuild = handoff('build-old', { dependsOn: ['research'] });
    const newBuild = handoff('build-new', {
      dependsOn: ['research'],
      supersedes: 'build-old',
    });
    const review = handoff('review', { dependsOn: ['build-old'] });
    const graph = resolveAgentBusGraph(
      [review, oldBuild, research, newBuild],
      new Set(['research', 'build-new']),
    );
    assert.deepEqual(graph.ordered.map((item) => item.id), [
      'research',
      'build-new',
      'review',
    ]);
    assert.deepEqual(graph.supersededIds, ['build-old']);
    assert.ok(graph.ready.some((item) => item.id === 'review'));
  });

  it('rejects missing dependencies and dependency cycles', () => {
    assert.throws(
      () => resolveAgentBusGraph([handoff('a', { dependsOn: ['missing'] })]),
      AgentBusInvariantError,
    );
    assert.throws(
      () => resolveAgentBusGraph([
        handoff('a', { dependsOn: ['b'] }),
        handoff('b', { dependsOn: ['a'] }),
      ]),
      /cycle/i,
    );
  });
});

describe('AgentBus v2 mutation receipts', () => {
  it('enforces workspace-relative scope patterns', () => {
    assert.equal(agentBusScopeAllows(['src/**', 'docs/*.md'], 'src/app/page.ts'), true);
    assert.equal(agentBusScopeAllows(['src/**', 'docs/*.md'], 'docs/plan.md'), true);
    assert.equal(agentBusScopeAllows(['src/**'], 'scripts/deploy.ts'), false);
    assert.equal(agentBusScopeAllows(['**'], '../outside.ts'), false);
    assert.equal(agentBusScopeAllows(['**'], 'C:\\outside.ts'), false);
  });

  it('accepts verified owned changes and rejects stale or out-of-scope work', () => {
    const task = handoff('build', {
      payload: {
        scope: ['src/**'],
        artifactPath: 'artifacts/build.json',
        budgetTokens: 10_000,
        budgetMs: 10_000,
      },
    });
    const claim = {
      handoffId: task.id,
      path: 'src/app.ts',
      fencingToken: 'fresh-token',
      generation: 2,
      expiresAt: new Date(5_000).toISOString(),
    };
    const receipt = {
      version: 1 as const,
      handoffId: task.id,
      artifactPath: 'artifacts/build.json',
      changedFiles: ['src/app.ts'],
      claims: [{ path: 'src/app.ts', fencingToken: 'fresh-token', generation: 2 }],
      checks: [{ name: 'unit', passed: true }],
      durationMs: 2_000,
      tokensUsed: 1_000,
    };
    assert.deepEqual(validateAgentBusCompletion(task, receipt, [claim], 4_000), { ok: true });
    assert.match(
      failureReason(validateAgentBusCompletion(task, {
        ...receipt,
        claims: [{ path: 'src/app.ts', fencingToken: 'stale-token', generation: 1 }],
      }, [claim], 4_000)),
      /stale/i,
    );
    assert.match(
      failureReason(validateAgentBusCompletion(task, {
        ...receipt,
        changedFiles: ['scripts/deploy.ts'],
        claims: [],
      }, [claim], 4_000)),
      /outside/i,
    );
    assert.match(
      failureReason(validateAgentBusCompletion(task, {
        ...receipt,
        tokensUsed: 10_001,
      }, [claim], 4_000)),
      /token budget/i,
    );
  });
});

describe('AgentBus v2 retry bounds', () => {
  it('escalates after the declared stall threshold', () => {
    assert.equal(agentBusRetryDecision({ priorAttempts: 1, stallThreshold: 2 }), 'retry');
    assert.equal(agentBusRetryDecision({ priorAttempts: 2, stallThreshold: 2 }), 'escalate');
  });
});

describe('AgentBus v2 event replay', () => {
  const event = (
    eventId: string,
    sequence: number,
    type: AgentBusLedgerEvent['type'],
  ): AgentBusLedgerEvent => ({
    eventId,
    sequence,
    handoffId: 'build',
    type,
    at: new Date(sequence * 1_000).toISOString(),
    actor: 'coordinator',
  });

  it('replays a legal lifecycle and ignores exact duplicate events', () => {
    const created = event('e1', 1, 'CREATED');
    const replay = replayAgentBusEvents([
      created,
      created,
      event('e2', 2, 'CLAIMED'),
      event('e3', 3, 'STARTED'),
      event('e4', 4, 'VERIFYING'),
      event('e5', 5, 'COMPLETED'),
    ]);
    assert.equal(replay.acceptedEvents.length, 5);
    assert.equal(replay.stateByHandoff.get('build'), 'complete');
  });

  it('rejects illegal transitions, stale sequences, and mutated duplicate IDs', () => {
    assert.throws(
      () => replayAgentBusEvents([event('e1', 1, 'CREATED'), event('e2', 2, 'COMPLETED')]),
      /illegal/i,
    );
    assert.throws(
      () => replayAgentBusEvents([event('e1', 2, 'CREATED'), event('e2', 1, 'CLAIMED')]),
      /sequence/i,
    );
    assert.throws(
      () => replayAgentBusEvents([
        event('e1', 1, 'CREATED'),
        { ...event('e1', 1, 'CREATED'), actor: 'different' },
      ]),
      /reused/i,
    );
  });
});
