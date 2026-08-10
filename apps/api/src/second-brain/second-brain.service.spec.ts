import assert from 'node:assert/strict';
import test from 'node:test';
import { SecondBrainService } from './second-brain.service';

test('Second Brain defaults to a live Gemini Flash model and never calls DeepSeek', async () => {
  const originalFetch = globalThis.fetch;
  const originalGemini = process.env.GEMINI_API_KEY;
  const originalPrimary = process.env.SECOND_BRAIN_PRIMARY_MODEL;
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];

  process.env.GEMINI_API_KEY = 'test-gemini-key';
  delete process.env.SECOND_BRAIN_PRIMARY_MODEL;
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({
      url: String(input),
      body: JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ choices: [{ message: { content: 'Looks good.' } }] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;

  try {
    const service = new SecondBrainService(
      { resolveApiKey: async () => null } as never,
      {
        getDecryptedPlatformGeminiKey: async () => null,
        getDecryptedPlatformGlmKey: async () => null,
      } as never,
    );
    const result = await service.critique({ agentOutput: 'A proposed implementation.' });

    assert.deepEqual(result, { text: 'Looks good.', provider: 'gemini-flash' });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions');
    assert.equal(calls[0].body.model, 'gemini-3.5-flash');
    assert.doesNotMatch(calls[0].url, /deepseek/i);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalGemini === undefined) delete process.env.GEMINI_API_KEY;
    else process.env.GEMINI_API_KEY = originalGemini;
    if (originalPrimary === undefined) delete process.env.SECOND_BRAIN_PRIMARY_MODEL;
    else process.env.SECOND_BRAIN_PRIMARY_MODEL = originalPrimary;
  }
});
