import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildFounderIdeMessage,
  isDispatchForNode,
  isFounderIdeProvider,
  type PendingDispatch,
} from '../src/founder-ide-dispatch-protocol';
import { EventEmitter } from 'node:events';
import { sendFounderIdeRequestAndWait } from '../src/founder-ide-dispatch-result';
import type { IpcMessage } from 'founder-ide-extension/ipc';

function dispatch(prompt: string): PendingDispatch {
  return {
    id: 'dispatch-123',
    nodeId: 'node-abc',
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

  it('refuses unbound and cross-computer dispatches', () => {
    const exact = dispatch('hello');
    assert.equal(isDispatchForNode(exact, 'node-abc'), true);
    assert.equal(isDispatchForNode(exact, 'node-other'), false);
    assert.equal(
      isDispatchForNode({ ...exact, nodeId: null }, 'node-abc'),
      false,
    );
  });

  it('starts listening before sending so an immediate IDE result is not lost', async () => {
    class ImmediateClient extends EventEmitter {
      send(message: IpcMessage): boolean {
        if (message.type === 'chatPrompt') {
          this.emit('message', {
            type: 'chatPromptResult',
            requestId: message.requestId,
            delivered: true,
            nonce: 'nonce-response-1234567890',
            ts: new Date().toISOString(),
          } satisfies IpcMessage);
        }
        return true;
      }
    }

    const client = new ImmediateClient();
    const request = buildFounderIdeMessage(dispatch('Review the current workspace.'));
    const result = JSON.parse(
      await sendFounderIdeRequestAndWait(client, request),
    ) as { kind: string; delivered: boolean };

    assert.deepEqual(result, { kind: 'chat', delivered: true });
    assert.equal(client.listenerCount('message'), 0);
  });

  it('removes the response listener when the authenticated pipe cannot send', async () => {
    class ClosedClient extends EventEmitter {
      send(): boolean {
        return false;
      }
    }

    const client = new ClosedClient();
    const request = buildFounderIdeMessage(dispatch('Review the current workspace.'));

    await assert.rejects(
      sendFounderIdeRequestAndWait(client, request),
      /authenticated pipe is unavailable/,
    );
    assert.equal(client.listenerCount('message'), 0);
  });
});
