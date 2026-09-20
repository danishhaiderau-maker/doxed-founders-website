import assert from 'node:assert/strict';
import test from 'node:test';
import { TradingAgentsService } from './trading-agents.service';

function makeService(analyzerSummary: Record<string, unknown> | null) {
  const botBridge = {
    fetchPublicShowcaseState: async () => ({
      fresh_epoch_id: 'epoch-current',
      state_integrity: { snapshot_age_sec: 0 },
    }),
    fetchAnalyzerMirrorReceipt: async () => null,
    fetchAnalyzerSummary: async () => analyzerSummary,
  };
  return new TradingAgentsService(
    {} as never,
    botBridge as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );
}

test('bot health reports unreachable when receipt and summary retrieval both fail', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response('{}', { status: 503 });
  try {
    const health = await makeService(null).getBotHealth('conservative-btc');
    assert.equal(health.analyzerMirror?.status, 'unreachable');
    assert.equal(health.analyzerMirror?.fresh, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('bot health keeps legacy summary unbound when receipt retrieval fails', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response('{}', { status: 503 });
  try {
    const health = await makeService({
      mirror_available: true,
      mirror_status: {
        uploaded_at: new Date().toISOString(),
        size: 100,
      },
    }).getBotHealth('conservative-btc');
    assert.equal(health.analyzerMirror?.status, 'unbound');
    assert.equal(health.analyzerMirror?.fresh, false);
    assert.equal(health.analyzerMirror?.epochBound, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
