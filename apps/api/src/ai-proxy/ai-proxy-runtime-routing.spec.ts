import assert from 'node:assert/strict';
import test from 'node:test';
import { plainToInstance } from 'class-transformer';
import { validate } from 'class-validator';
import { AiProxyRuntimeService } from './ai-proxy-runtime.service';
import { ModelRouterService } from '../founder-ai-runtime/model-router.service';
import { ChatCompletionRequestDto } from './dto/ai-proxy.dto';

function runtimeWithDecision(model: string, provider = 'deepseek') {
  const calls: Array<{ intent: string }> = [];
  const runtime = new AiProxyRuntimeService(
    {} as never,
    {} as never,
    { route: () => { throw new Error('legacy route should not be used'); } } as never,
    {} as never,
    {
      route: async (request: { intent: string }) => {
        calls.push({ intent: request.intent });
        return {
          chosenProvider: provider,
          chosenModel: model,
          candidates: [],
          cacheLevel: 'miss',
          cacheKey: 'test-cache-key',
        };
      },
    } as never,
    {} as never,
    {} as never,
    { classify: async () => ({ intent: 'reasoning' }) } as never,
    {} as never,
  );
  return { runtime, calls };
}

function realLegacyRouter() {
  return new ModelRouterService({
    getSyncConfig: () => ({
      deepseekFastModel: 'deepseek-v4-flash',
      deepseekCodingModel: 'deepseek-v4-pro',
      glmFastModel: 'glm-4-flash',
      glmCodingModel: 'glm-5.2',
    }),
    resolveRouteProviders: (route: unknown) => route,
  } as never);
}

function runtimeWithV2Failure() {
  return new AiProxyRuntimeService(
    {} as never,
    {} as never,
    realLegacyRouter(),
    {} as never,
    { route: async () => { throw new Error('simulated v2 outage'); } } as never,
    {} as never,
    {} as never,
    { classify: async () => ({ intent: 'reasoning' }) } as never,
    {} as never,
  );
}

const auth = { userId: 'user-test', nodeId: 'node-test' };
const messages = [{ role: 'user', content: 'Analyze this architecture in depth.' }];

test('founder-os-fast forces simple_qa before v2 routing and uses v4 flash', async () => {
  const { runtime, calls } = runtimeWithDecision('deepseek-coder-v2');
  const route = await runtime.decideRoute(auth, {
    model: 'founder-os-fast',
    messages,
  });
  assert.equal(calls[0]?.intent, 'simple_qa');
  assert.equal(route.intent, 'simple_qa');
  assert.equal(route.tier, 'fast');
  assert.equal(route.model, 'deepseek-v4-flash');
});

test('stale kimi decision cannot escape the supported gateway boundary', async () => {
  const { runtime } = runtimeWithDecision('kimi-k2', 'kimi');
  const route = await runtime.decideRoute(auth, {
    model: 'founder-os-code',
    messages,
  });
  assert.equal(route.providerKey, 'deepseek');
  assert.equal(route.model, 'deepseek-v4-pro');
  assert.equal(route.tier, 'code');
});

test('reasoning and code aliases route with forced intent and v4 pro', async () => {
  for (const [alias, intent] of [
    ['founder-os-reasoning', 'reasoning'],
    ['founder-os-code', 'code'],
  ] as const) {
    const { runtime, calls } = runtimeWithDecision('deepseek-coder-v2');
    const route = await runtime.decideRoute(auth, { model: alias, messages });
    assert.equal(calls[0]?.intent, intent);
    assert.equal(route.intent, intent);
    assert.equal(route.model, 'deepseek-v4-pro');
  }
});

test('founder-os-auto preserves inferred intent while normalizing stale model', async () => {
  const { runtime, calls } = runtimeWithDecision('deepseek-reasoner');
  const route = await runtime.decideRoute(auth, {
    model: 'founder-os-auto',
    messages,
  });
  assert.equal(calls[0]?.intent, 'reasoning');
  assert.equal(route.intent, 'reasoning');
  assert.equal(route.model, 'deepseek-v4-pro');
});

test('founder-os-fast stays fast/flash when v2 falls back to the legacy router', async () => {
  const runtime = runtimeWithV2Failure();
  const route = await runtime.decideRoute(auth, {
    model: 'founder-os-fast',
    messages,
  });
  assert.equal(route.intent, 'simple_qa');
  assert.equal(route.tier, 'fast');
  assert.equal(route.model, 'deepseek-v4-flash');
});

test('legacy fallback honors forced reasoning even for a simple prompt', async () => {
  const runtime = runtimeWithV2Failure();
  const route = await runtime.decideRoute(auth, {
    model: 'founder-os-reasoning',
    messages: [{ role: 'user', content: 'hello' }],
  });
  assert.equal(route.intent, 'reasoning');
  assert.equal(route.tier, 'reasoning');
  assert.equal(route.providerKey, 'deepseek');
  assert.equal(route.model, 'deepseek-v4-pro');
});

test('Founder Agent tool fields pass strict request validation', async () => {
  const request = plainToInstance(ChatCompletionRequestDto, {
    model: 'founder-os-reasoning',
    messages: [
      { role: 'assistant', content: '', tool_calls: [{ id: 'call-1', type: 'function' }] },
      { role: 'tool', content: 'file contents', tool_call_id: 'call-1' },
    ],
    stream: true,
    tools: [
      {
        type: 'function',
        function: {
          name: 'read_file',
          description: 'Read a workspace file.',
          parameters: { type: 'object', properties: { path: { type: 'string' } } },
        },
      },
    ],
    tool_choice: 'auto',
  });

  const errors = await validate(request, {
    whitelist: true,
    forbidNonWhitelisted: true,
  });
  assert.deepEqual(errors, []);
});

test('Founder Agent tools and tool history reach the upstream provider', async () => {
  const sentBodies: Array<Record<string, unknown>> = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_input, init) => {
    sentBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
    return new Response(JSON.stringify({ choices: [{ message: { content: 'ok' } }] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    const runtime = new AiProxyRuntimeService(
      {} as never,
      {} as never,
      {} as never,
      { resolveApiKey: async () => 'test-key' } as never,
      {} as never,
      {} as never,
      {} as never,
      {} as never,
      { buildMemoryContext: async () => '' } as never,
    );
    const tools = [
      {
        type: 'function',
        function: {
          name: 'read_file',
          description: 'Read a workspace file.',
          parameters: { type: 'object', properties: { path: { type: 'string' } } },
        },
      },
    ];

    const result = await runtime.invoke(
      auth,
      {
        model: 'founder-os-reasoning',
        messages: [
          {
            role: 'assistant',
            content: '',
            tool_calls: [{ id: 'call-1', type: 'function', function: { name: 'read_file', arguments: '{}' } }],
          },
          { role: 'tool', content: 'file contents', tool_call_id: 'call-1' },
        ],
        tools,
        tool_choice: 'auto',
      },
      {
        requestId: 'request-tools',
        providerKey: 'deepseek',
        model: 'deepseek-v4-pro',
        tier: 'code',
        intent: 'code',
        profile: 'balanced',
        candidates: [],
        cacheKey: null,
        cacheLevel: 'miss',
        promptHash: 'hash',
        flightRecorderHasDecisionRow: false,
      } as never,
    );

    assert.equal(result.ok, true);
    assert.deepEqual(sentBodies[0]?.tools, tools);
    assert.equal(sentBodies[0]?.tool_choice, 'auto');
    assert.deepEqual((sentBodies[0]?.messages as Array<Record<string, unknown>>)[0]?.tool_calls, [
      { id: 'call-1', type: 'function', function: { name: 'read_file', arguments: '{}' } },
    ]);
    assert.equal((sentBodies[0]?.messages as Array<Record<string, unknown>>)[1]?.tool_call_id, 'call-1');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
