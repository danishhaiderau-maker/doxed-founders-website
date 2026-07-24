import assert from 'node:assert/strict';
import test from 'node:test';
import { ServiceUnavailableException } from '@nestjs/common';
import { AiProxySpeechService } from './ai-proxy-speech.service';

test('managed speech fails closed when no platform speech key exists', async () => {
  const service = new AiProxySpeechService({
    getDecryptedPlatformGlmSpeechKey: async () => null,
  } as never);

  await assert.rejects(
    () => service.transcribeWav(new Uint8Array(44)),
    ServiceUnavailableException,
  );
});

test('managed speech keeps the platform key server-side', async () => {
  const originalFetch = globalThis.fetch;
  let authorization = '';
  let body: FormData | undefined;
  globalThis.fetch = async (_input, init) => {
    authorization = String(
      (init?.headers as Record<string, string> | undefined)?.Authorization ??
        '',
    );
    body = init?.body as FormData;
    return new Response(JSON.stringify({ text: 'ship the verified change' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const service = new AiProxySpeechService({
      getDecryptedPlatformGlmSpeechKey: async () => 'platform-secret',
    } as never);
    const result = await service.transcribeWav(new Uint8Array(44));

    assert.equal(authorization, 'Bearer platform-secret');
    assert.equal(body?.get('model'), 'glm-asr-2512');
    assert.deepEqual(result, {
      text: 'ship the verified change',
      provider: 'glm',
      model: 'glm-asr-2512',
      route: 'founder-managed-speech',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
