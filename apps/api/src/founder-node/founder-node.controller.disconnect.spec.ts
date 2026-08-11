import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { FounderNodeController } from './founder-node.controller';

describe('FounderNodeController self revoke', () => {
  it('revokes only the authenticated Founder Node identity', async () => {
    const calls: Array<{ userId: string; nodeId: string }> = [];
    const controller = new FounderNodeController(
      {
        revokeNode: async (userId: string, nodeId: string) => {
          calls.push({ userId, nodeId });
          return { success: true };
        },
      } as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
    );

    const result = await controller.revokeSelf({
      founderNode: {
        kind: 'founder-node',
        userId: 'owner-1',
        nodeId: 'node-1',
        nodeDbId: 'database-node-1',
      },
    });

    assert.deepEqual(result, { success: true });
    assert.deepEqual(calls, [{ userId: 'owner-1', nodeId: 'node-1' }]);
  });
});
