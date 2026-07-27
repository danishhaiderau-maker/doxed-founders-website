import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  FounderGoalCloudClient,
  mergeFounderGoalCloudState,
  synchronizeFounderGoalState,
  type FounderGoalCloudState,
} from './founder-goal-cloud';
import {
  createFounderGoalAmendmentDecision,
  enqueueFounderDecision,
  initialFounderGoalState,
  resolveFounderGoalUiDecision,
} from './founder-goal-state';

const credentials = {
  apiBaseUrl: 'https://founder.test',
  nodeId: 'node-1',
  nodeToken: 'private-node-token',
};

describe('Founder Goal cloud sync', () => {
  it('uses paired-node authentication without exposing credentials in the URL', async () => {
    let seenUrl = '';
    let seenAuthorization = '';
    const client = new FounderGoalCloudClient(async (input, init) => {
      seenUrl = String(input);
      seenAuthorization = String(
        (init?.headers as Record<string, string>)?.Authorization,
      );
      return jsonResponse(emptyCloudState());
    });

    await client.load(credentials, 'workspace-abc');

    assert.equal(
      seenUrl,
      'https://founder.test/api/founder-node/goal-control?workspaceKey=workspace-abc',
    );
    assert.equal(
      seenAuthorization,
      'FounderNode node-1:private-node-token',
    );
    assert.doesNotMatch(seenUrl, /private-node-token/);
  });

  it('merges a server decision into the same workspace without dropping local history', () => {
    const local = initialFounderGoalState('Payments', new Date('2026-07-27T00:00:00Z'));
    const localDecision = createFounderGoalAmendmentDecision(
      local,
      'Ship the Payments onboarding',
      new Date('2026-07-27T00:01:00Z'),
    );
    const localWithDecision = enqueueFounderDecision(local, localDecision);
    const cloud = emptyCloudState();
    cloud.goal = cloudGoal(local);
    cloud.decisions = [{
      ...cloudDecision(local, localDecision),
      id: 'remote-decision',
      title: 'Choose a deployment region',
      question: 'Which deployment region should Founder use?',
    }];

    const merged = mergeFounderGoalCloudState(localWithDecision, cloud);

    assert.deepEqual(
      new Set(merged.decisions.map((item) => item.id)),
      new Set([localDecision.id, 'remote-decision']),
    );
    assert.equal(merged.objective, local.objective);
  });

  it('uploads an offline founder answer and returns the durable resolution', async () => {
    const base = initialFounderGoalState(
      'Payments',
      new Date('2026-07-27T00:00:00Z'),
    );
    const decision = createFounderGoalAmendmentDecision(
      base,
      'Ship the Payments onboarding',
      new Date('2026-07-27T00:01:00Z'),
    );
    const local = resolveFounderGoalUiDecision(
      enqueueFounderDecision(base, decision),
      {
        decisionId: decision.id,
        selectedOptionId: 'keep',
        now: new Date('2026-07-27T00:02:00Z'),
      },
    );
    const cloud = emptyCloudState();
    cloud.goal = cloudGoal(base);
    cloud.decisions = [cloudDecision(base, decision)];
    const calls: string[] = [];
    const client = new FounderGoalCloudClient(async (input, init) => {
      const url = new URL(String(input));
      calls.push(`${init?.method ?? 'GET'} ${url.pathname}`);
      if (url.pathname.endsWith('/decisions/resolve')) {
        const body = JSON.parse(String(init?.body)) as {
          requestId: string;
          selectedOptionId: string;
        };
        cloud.decisions[0]!.status = 'resolved';
        cloud.resolutions = [{
          requestId: body.requestId,
          selectedOptionId: body.selectedOptionId,
          resolvedAt: '2026-07-27T00:02:00.000Z',
          resolvedBy: 'founder',
        }];
      }
      return jsonResponse(cloud);
    });

    const synced = await synchronizeFounderGoalState(
      local,
      credentials,
      'workspace-abc',
      client,
    );

    assert.ok(
      calls.includes(
        'POST /api/founder-node/goal-control/decisions/resolve',
      ),
    );
    assert.equal(synced.decisions[0]?.status, 'resolved');
    assert.equal(synced.decisions[0]?.selectedOptionId, 'keep');
  });
});

function emptyCloudState(): FounderGoalCloudState {
  return {
    goal: null,
    decisions: [],
    resolutions: [],
    updatedAt: '2026-07-27T00:00:00.000Z',
  };
}

function cloudGoal(goal: ReturnType<typeof initialFounderGoalState>) {
  return {
    id: goal.id,
    version: goal.version,
    objective: goal.objective,
    constraints: [],
    successEvidence: [],
    status: goal.status,
    updatedAt: goal.updatedAt,
  } as NonNullable<FounderGoalCloudState['goal']>;
}

function cloudDecision(
  goal: ReturnType<typeof initialFounderGoalState>,
  decision: ReturnType<typeof createFounderGoalAmendmentDecision>,
) {
  return {
    id: decision.id,
    goalId: goal.id,
    goalVersion: goal.version,
    kind: decision.kind,
    risk: decision.risk,
    title: decision.title,
    question: decision.question,
    options: decision.options.map((option) => option && ({
      ...option,
      impact: option.description,
    })).filter(Boolean) as Array<{
      id: string;
      label: string;
      description: string;
      impact: string;
      recommended?: boolean;
    }>,
    allowCustomAnswer: decision.allowCustomAnswer,
    blockingTaskIds: decision.blockingTaskIds,
    independentWorkMayContinue: decision.independentWorkMayContinue,
    evidence: decision.evidence,
    createdAt: decision.createdAt,
    status: decision.status,
    proposedGoalObjective: decision.proposedGoalObjective,
    researchFindings: [],
  } as FounderGoalCloudState['decisions'][number];
}

function jsonResponse(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}
