import assert from 'node:assert/strict';
import test from 'node:test';
import { fetchAnalyzerSessionSummary } from './api';

test('session summary uses the browser-compatible neutral route', async () => {
  const originalFetch = globalThis.fetch;
  let requestedUrl = '';
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    requestedUrl = String(input);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;

  try {
    const result = await fetchAnalyzerSessionSummary('conservative-btc');
    assert.equal(
      requestedUrl,
      '/api/trading-agents/conservative-btc/session-summary',
    );
    assert.equal(result.ok, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
