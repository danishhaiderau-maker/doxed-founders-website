import assert from 'node:assert/strict';
import test from 'node:test';
import { cancelIdeDispatch, dispatchToIdeSession, type IdeDispatchStatus } from './api';
import { FOUNDER_IDE_DISPATCH_PROVIDER, remoteDispatchView } from './founder-remote-dispatch-view';

function captureFetch(body: unknown) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = original; } };
}

function status(overrides: Partial<IdeDispatchStatus>): IdeDispatchStatus {
  return {
    id: 'd1', status: 'REMOTE_PENDING', result: null, dispatchedAt: null,
    createdAt: '2026-09-29T00:00:00.000Z', sessionId: 's1', delivered: false, failed: false,
    ...overrides,
  };
}

test('Founder IDE dispatch names the exact paired node', async () => {
  const f = captureFetch({ id: 'd1', status: 'pending' });
  try {
    await dispatchToIdeSession('tok', 'founder-next-session:app', 'build it', FOUNDER_IDE_DISPATCH_PROVIDER, 'node-abc');
    assert.equal(f.calls[0]?.url, '/api/ide-bridge/sessions/founder-next-session%3Aapp/dispatch');
    assert.equal(f.calls[0]?.init?.method, 'POST');
    assert.deepEqual(JSON.parse(String(f.calls[0]?.init?.body)), {
      prompt: 'build it', ideProvider: 'founder-ide', targetNodeId: 'node-abc',
    });
  } finally {
    f.restore();
  }
});

test('legacy Cursor dispatch without a node keeps its original body', async () => {
  const f = captureFetch({ id: 'd2', status: 'PENDING' });
  try {
    await dispatchToIdeSession('tok', 'composer-1', 'hi');
    assert.deepEqual(JSON.parse(String(f.calls[0]?.init?.body)), { prompt: 'hi', ideProvider: 'cursor' });
  } finally {
    f.restore();
  }
});

test('owner cancel posts to the dispatch cancel route with auth', async () => {
  const f = captureFetch(status({ status: 'REMOTE_CLAIMED', executionStatus: 'cancellation_requested', cancellationRequested: true }));
  try {
    const s = await cancelIdeDispatch('tok', 'd1', 'stop');
    assert.equal(f.calls[0]?.url, '/api/ide-bridge/dispatch/d1/cancel');
    assert.equal(f.calls[0]?.init?.method, 'POST');
    assert.equal((f.calls[0]?.init?.headers as Record<string, string>).Authorization, 'Bearer tok');
    assert.deepEqual(JSON.parse(String(f.calls[0]?.init?.body)), { reason: 'stop' });
    assert.equal(s.executionStatus, 'cancellation_requested');
  } finally {
    f.restore();
  }
});

test('pending and claimed remote runs are cancellable and still polling', () => {
  for (const executionStatus of ['pending', 'claimed'] as const) {
    const v = remoteDispatchView(status({ executionStatus }));
    assert.equal(v.cancellable, true);
    assert.equal(v.pending, true);
    assert.equal(v.terminal, false);
  }
});

test('a requested cancel hides the button but keeps polling until the desktop stops', () => {
  const v = remoteDispatchView(status({ status: 'REMOTE_CLAIMED', executionStatus: 'cancellation_requested' }));
  assert.equal(v.cancellable, false);
  assert.equal(v.terminal, false);
  assert.match(v.label, /Cancel requested/);
});

test('an owner-cancelled run ends as Cancelled, not a generic failure label', () => {
  const v = remoteDispatchView(status({
    status: 'REMOTE_FAILED', executionStatus: 'failed', failed: true,
    error: 'Cancelled by the Founder owner: Cancelled by the Founder owner from the website.',
  }));
  assert.equal(v.terminal, true);
  assert.equal(v.cancellable, false);
  assert.match(v.label, /^Cancelled: /);
});

test('complete, failed and expired remote runs are terminal and not cancellable', () => {
  assert.deepEqual(
    remoteDispatchView(status({ status: 'REMOTE_COMPLETE', executionStatus: 'complete' })),
    { label: 'Completed on your paired desktop', pending: false, terminal: true, failed: false, cancellable: false },
  );
  const failed = remoteDispatchView(status({ status: 'REMOTE_FAILED', executionStatus: 'failed', failed: true, error: 'exit 1' }));
  assert.equal(failed.label, 'Failed: exit 1');
  const expired = remoteDispatchView(status({ status: 'REMOTE_EXPIRED', executionStatus: 'expired', failed: true, error: 'Request expired' }));
  assert.equal(expired.terminal, true);
  assert.equal(expired.cancellable, false);
});

test('legacy Cursor dispatch statuses never offer cancel', () => {
  assert.equal(remoteDispatchView(status({ status: 'PENDING' })).cancellable, false);
  assert.equal(remoteDispatchView(status({ status: 'DISPATCHED', delivered: true })).label, 'Delivered to Founder IDE');
  assert.match(remoteDispatchView(status({ status: 'DISPATCHED', failed: true, result: 'error: x' })).label, /^Failed/);
});
