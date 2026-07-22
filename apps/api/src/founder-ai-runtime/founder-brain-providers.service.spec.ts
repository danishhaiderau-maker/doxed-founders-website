import assert from 'node:assert/strict';
import test from 'node:test';
import { FounderBrainProvidersService } from './founder-brain-providers.service';

test('managed DeepSeek health fails closed and recovers through the probe', async () => {
  const originalFetch = globalThis.fetch;
  let ok = false;
  let requestedModel = '';
  globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? '{}')) as { model?: string };
    requestedModel = body.model ?? '';
    return new Response(ok ? '{}' : 'invalid key', { status: ok ? 200 : 401 });
  }) as typeof fetch;

  try {
    const service = new FounderBrainProvidersService(
      {} as never,
      {
        getDecryptedPlatformDeepseekKey: async () => 'secret-test-key',
        getDecryptedPlatformPromoDeepseekKey: async () => null,
      } as never,
      { getDecryptedKey: async () => null } as never,
    );

    const failed = await service.testProvider('deepseek');
    assert.equal(failed.ok, false);
    assert.equal(requestedModel, 'deepseek-v4-flash');
    assert.equal(await service.resolveApiKey('deepseek'), null);

    ok = true;
    const recovered = await service.testProvider('deepseek');
    assert.equal(recovered.ok, true);
    assert.equal(await service.resolveApiKey('deepseek'), 'secret-test-key');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
