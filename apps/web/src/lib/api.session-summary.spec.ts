import assert from 'node:assert/strict';
import test from 'node:test';
import { fetchAnalyzerSessionSummary, refreshPausedTradingAgentCredentials } from './api';

function withApiLabMode<T>(fn: () => Promise<T>): Promise<T> {
  const prev = process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
  process.env.NEXT_PUBLIC_BACKEND_API_RETIRED = '0';
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      if (prev === undefined) delete process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
      else process.env.NEXT_PUBLIC_BACKEND_API_RETIRED = prev;
    });
}

test('session summary uses the browser-compatible neutral route', async () => {
  await withApiLabMode(async () => {
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
});

test('paused credential refresh uses the dedicated authenticated non-hire route', async () => {
  await withApiLabMode(async () => {
    const originalFetch = globalThis.fetch;
    let requestedUrl = '';
    let requestedInit: RequestInit | undefined;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      requestedUrl = String(input);
      requestedInit = init;
      return new Response(JSON.stringify({
        ok: true, status: 'PAUSED', armed: false, resumed: false,
        chargedDdollar: 0, marginTransferRequested: false,
        authenticatedAudit: { known: true, flat: true, observedAt: new Date().toISOString() },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;
    try {
      const result = await refreshPausedTradingAgentCredentials(
        'conservative-btc',
        { exchangeProvider: 'bitfinex', apiKey: 'candidate', apiSecret: 'candidate-secret' },
        'session-token',
      );
      assert.equal(requestedUrl, '/api/trading-agents/conservative-btc/credentials/refresh-paused');
      assert.equal(requestedInit?.method, 'POST');
      assert.equal((requestedInit?.headers as Record<string, string>).Authorization, 'Bearer session-token');
      assert.equal(result.status, 'PAUSED');
      assert.equal(result.chargedDdollar, 0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
