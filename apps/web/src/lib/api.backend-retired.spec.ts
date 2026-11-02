import assert from 'node:assert/strict';
import test from 'node:test';
import { BackendApiRetiredError } from './backend-api-retired';
import { fetchAnalyzerSessionSummary } from './api';

test('apiFetch short-circuits with BackendApiRetiredError and never calls fetch', async () => {
  const prev = process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
  delete process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;

  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = (async () => {
    fetchCalls += 1;
    return new Response('{}', { status: 200 });
  }) as typeof fetch;

  try {
    await assert.rejects(
      () => fetchAnalyzerSessionSummary('conservative-btc'),
      (err: unknown) => {
        assert.ok(err instanceof BackendApiRetiredError);
        return true;
      },
    );
    assert.equal(fetchCalls, 0);
  } finally {
    globalThis.fetch = originalFetch;
    if (prev === undefined) delete process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
    else process.env.NEXT_PUBLIC_BACKEND_API_RETIRED = prev;
  }
});
