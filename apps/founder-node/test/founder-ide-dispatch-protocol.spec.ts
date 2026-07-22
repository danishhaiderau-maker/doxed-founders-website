import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildFounderIdeMessage,
  isFounderIdeProvider,
  type PendingDispatch,
} from '../src/founder-ide-dispatch-protocol';

function dispatch(prompt: string): PendingDispatch {
  return {
    id: 'dispatch-123',
    sessionId: 'founder-ide:node-abc',
    prompt,
    ideProvider: 'founder-ide',
  };
}

describe('Founder IDE relay protocol', () => {
  it('delivers a plain phone prompt into Founder Chat', () => {
    const message = buildFounderIdeMessage(dispatch('Review the failing test.'));
    assert.equal(message.type, 'chatPrompt');
    assert.equal(message.requestId, 'dispatch-123');
    assert.equal(message.prompt, 'Review the failing test.');
  });

  it('maps structured workspace reads and bounds the request identity', () => {
    const message = buildFounderIdeMessage(
      dispatch(JSON.stringify({ founderIdeAction: { type: 'workspaceReadRequest', path: 'src', maxEntries: 25 } })),
    );
    assert.equal(message.type, 'workspaceReadRequest');
    assert.equal(message.requestId, 'dispatch-123');
    assert.equal(message.path, 'src');
    assert.equal(message.maxEntries, 25);
  });

  it('preserves structured edits for local diff review', () => {
    const message = buildFounderIdeMessage(
      dispatch(JSON.stringify({
        founderIdeAction: {
          type: 'proposedEdit',
          path: 'src/app.ts',
          edit: { kind: 'patch', anchor: 'old', content: 'new' },
        },
      })),
    );
    assert.equal(message.type, 'proposedEdit');
    assert.deepEqual(message.edit, { kind: 'patch', anchor: 'old', content: 'new' });
  });

  it('defaults an unknown command risk to mutation', () => {
    const message = buildFounderIdeMessage(
      dispatch(JSON.stringify({ founderIdeAction: { type: 'commandRequest', command: 'npm test', risk: 'unknown' } })),
    );
    assert.equal(message.type, 'commandRequest');
    assert.equal(message.risk, 'mutation');
  });

  it('recognizes Founder IDE compatibility providers only', () => {
    assert.equal(isFounderIdeProvider('founder-ide'), true);
    assert.equal(isFounderIdeProvider('vscode'), true);
    assert.equal(isFounderIdeProvider('cursor'), false);
  });
});
