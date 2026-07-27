import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import { afterEach, describe, it } from 'node:test';
import {
  initialFounderGoalState,
  createFounderHousekeepingDecision,
  enqueueFounderDecision,
  resolveFounderGoalUiDecision,
} from './founder-goal-state';
import { auditWorkspaceHousekeeping } from './workspace-housekeeping-audit';
import { applyApprovedHousekeeping } from './workspace-housekeeping-executor';

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe('workspace housekeeping executor', () => {
  it('deletes only an exact approved regenerable snapshot and writes a restore receipt', async () => {
    const root = await createWorkspace();
    const cache = path.join(root, '.cache');
    await fs.mkdir(cache, { recursive: true });
    await fs.writeFile(path.join(cache, 'generated.bin'), 'generated');
    const [candidate] = (await auditWorkspaceHousekeeping(root)).candidates;
    assert.ok(candidate);
    const scoped = {
      ...candidate,
      id: `${path.basename(root)}:${candidate.id}`,
    };
    const state = initialFounderGoalState('Workspace');
    const decision = createFounderHousekeepingDecision(state, [scoped]);
    const approved = resolveFounderGoalUiDecision(
      enqueueFounderDecision(state, decision),
      {
        decisionId: decision.id,
        selectedOptionId: 'approve_selected',
        selectedCandidateIds: [scoped.id],
      },
    ).decisions[0]!;
    const checkpointDirectory = path.join(root, '.receipts');

    const receipt = await applyApprovedHousekeeping({
      decision: approved,
      workspaces: [{ name: path.basename(root), path: root }],
      checkpointDirectory,
    });

    await assert.rejects(fs.stat(cache));
    assert.equal(receipt.reclaimedBytes, scoped.sizeBytes);
    const checkpoint = JSON.parse(
      await fs.readFile(receipt.checkpointPath, 'utf8'),
    ) as { status: string; entries: Array<{ restoreInstructions: string }> };
    assert.equal(checkpoint.status, 'complete');
    assert.match(checkpoint.entries[0]!.restoreInstructions, /regenerate/i);
  });

  it('fails closed when an approved path changes after review', async () => {
    const root = await createWorkspace();
    const cache = path.join(root, '.cache');
    await fs.mkdir(cache, { recursive: true });
    await fs.writeFile(path.join(cache, 'generated.bin'), 'before');
    const [candidate] = (await auditWorkspaceHousekeeping(root)).candidates;
    assert.ok(candidate);
    const scoped = {
      ...candidate,
      id: `${path.basename(root)}:${candidate.id}`,
    };
    const state = initialFounderGoalState('Workspace');
    const decision = createFounderHousekeepingDecision(state, [scoped]);
    const approved = resolveFounderGoalUiDecision(
      enqueueFounderDecision(state, decision),
      {
        decisionId: decision.id,
        selectedOptionId: 'approve_selected',
        selectedCandidateIds: [scoped.id],
      },
    ).decisions[0]!;
    await fs.writeFile(path.join(cache, 'later.bin'), 'changed after approval');

    await assert.rejects(
      applyApprovedHousekeeping({
        decision: approved,
        workspaces: [{ name: path.basename(root), path: root }],
        checkpointDirectory: path.join(root, '.receipts'),
      }),
      /changed after review/,
    );
    assert.equal((await fs.stat(cache)).isDirectory(), true);
  });

  it('never executes research, archive, or unresolved decisions', async () => {
    const root = await createWorkspace();
    const state = initialFounderGoalState('Workspace');
    const decision = createFounderHousekeepingDecision(state, [{
      id: 'review-only',
      path: 'backup.zip',
      workspaceFolder: path.basename(root),
      sizeBytes: 10,
      category: 'archive',
      evidence: ['Review manually.'],
      referencedBy: [],
      recommendedAction: 'delete',
      reversible: false,
      auditFingerprint: 'a'.repeat(64),
      restorePlan: {
        kind: 'manual_review',
        instructions: 'Review manually.',
      },
    }]);
    const queued = enqueueFounderDecision(state, decision).decisions[0]!;

    await assert.rejects(
      applyApprovedHousekeeping({
        decision: queued,
        workspaces: [{ name: path.basename(root), path: root }],
        checkpointDirectory: path.join(root, '.receipts'),
      }),
      /explicit resolved founder approval/,
    );
  });
});

async function createWorkspace() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'founder-housekeeping-'));
  roots.push(root);
  return root;
}
