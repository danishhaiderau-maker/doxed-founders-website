import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import { ServiceUnavailableException } from '@nestjs/common';
import { AiInvokerService } from './ai-invoker.service';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function service(options?: { apiKey?: string | null }) {
  const events: string[] = [];
  const prisma = {
    aiRoutingProvider: {
      findUnique: async () => ({
        key: 'deepseek',
        label: 'DeepSeek',
        baseUrl: 'https://api.deepseek.com/v1',
        defaultModel: 'deepseek-v4-flash',
        adapter: 'openai_compat',
        enabled: true,
      }),
    },
  };
  const routing = {
    getDecryptedKey: async () => options?.apiKey ?? null,
    seedDefaults: async () => undefined,
  };
  const adoption = {
    recordAiUsage: async () => undefined,
  };
  const brainProviders = {
    resolveApiKey: async () => options?.apiKey ?? null,
  };
  return {
    events,
    instance: new AiInvokerService(
      prisma as never,
      routing as never,
      adoption as never,
      brainProviders as never,
    ),
  };
}

describe('AiInvokerService provider lifecycle', () => {
  it('validates credentials, reserves before fetch, then reconciles usage', async () => {
    const { instance, events } = service({ apiKey: 'managed-secret' });
    globalThis.fetch = (async () => {
      events.push('fetch');
      return new Response(
        JSON.stringify({
          choices: [{ message: { content: 'done' } }],
          usage: { prompt_tokens: 12, completion_tokens: 4 },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }) as typeof fetch;

    const result = await instance.invoke({
      section: 'copilot',
      providerKey: 'deepseek',
      messages: [{ role: 'user', content: 'hello' }],
      lifecycle: {
        beforeProvider: async () => { events.push('reserve'); },
        afterProvider: async () => { events.push('reconcile'); },
        providerUncertain: async () => { events.push('uncertain'); },
      },
    });

    assert.equal(result.content, 'done');
    assert.deepEqual(events, ['reserve', 'fetch', 'reconcile']);
  });

  it('does not reserve when the managed key is missing', async () => {
    const { instance, events } = service({ apiKey: null });

    await assert.rejects(
      instance.invoke({
        section: 'copilot',
        providerKey: 'deepseek',
        messages: [{ role: 'user', content: 'hello' }],
        lifecycle: {
          beforeProvider: async () => { events.push('reserve'); },
        },
      }),
      ServiceUnavailableException,
    );
    assert.deepEqual(events, []);
  });

  it('marks a started provider request uncertain when upstream fails', async () => {
    const { instance, events } = service({ apiKey: 'managed-secret' });
    globalThis.fetch = (async () => {
      events.push('fetch');
      return new Response('upstream failed', { status: 503 });
    }) as typeof fetch;

    await assert.rejects(
      instance.invoke({
        section: 'copilot',
        providerKey: 'deepseek',
        messages: [{ role: 'user', content: 'hello' }],
        lifecycle: {
          beforeProvider: async () => { events.push('reserve'); },
          afterProvider: async () => { events.push('reconcile'); },
          providerUncertain: async () => { events.push('uncertain'); },
        },
      }),
      ServiceUnavailableException,
    );
    assert.deepEqual(events, ['reserve', 'fetch', 'uncertain']);
  });
});

