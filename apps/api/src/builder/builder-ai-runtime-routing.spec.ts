import assert from 'node:assert/strict';
import test from 'node:test';
import { BuilderService } from './builder.service';
import { FounderAiRuntimeService } from '../founder-ai-runtime/founder-ai-runtime.service';
import { ProviderEgressAuditService } from '../founder-ai-runtime/provider-egress-audit.service';

function runtimeHarness() {
  const audit = new ProviderEgressAuditService();
  const promptCache = {
    buildKey: () => 'quick-build-key',
    get: async () => null,
    set: async () => undefined,
  };
  const modelRouter = {
    route: () => ({
      intent: 'code',
      providerKey: 'deepseek',
      model: 'deepseek-v4-pro',
      tier: 'code',
    }),
  };
  const contextBuilder = {
    prepareRequest: <T>(request: T) => request,
    maxOutputTokens: () => 1_234,
  };
  const runtime = new FounderAiRuntimeService(
    promptCache as never,
    modelRouter as never,
    contextBuilder as never,
    audit,
  );
  return { audit, runtime };
}

function builderHarness(runtime: FounderAiRuntimeService, audit: ProviderEgressAuditService) {
  const invocations: Array<Record<string, unknown>> = [];
  const service = Object.create(BuilderService.prototype) as unknown as {
    tryRoutedInvoker: (
      section: 'copilot' | 'quick_build' | 'founder_draft',
      userId: string,
      system: string,
      userPrompt: string,
    ) => Promise<unknown>;
  };

  Object.assign(service, {
    founderAiRuntime: runtime,
    logger: { warn: () => undefined },
    aiInvoker: {
      resolveProviderKey: async () => 'deepseek',
      invoke: async (options: Record<string, unknown>) => {
        invocations.push(options);
        audit.record({
          adapterName: 'ai-invoker.openai-compatible',
          provider: 'deepseek',
          callSiteId: 'ai_routing.quick_build',
          budgetDomain: 'founder_managed',
        });
        return {
          content: '{"ideaTitle":"Runtime governed"}',
          usage: { promptTokens: 12, completionTokens: 7 },
          provider: 'deepseek',
          model: 'deepseek-v4-flash',
        };
      },
    },
  });

  return { service, invocations };
}

test('quick build routed invocation enters Founder Runtime without changing provider selection', async () => {
  const previous = process.env.AI_RUNTIME_ENABLED;
  process.env.AI_RUNTIME_ENABLED = 'true';
  try {
    const { audit, runtime } = runtimeHarness();
    const { service, invocations } = builderHarness(runtime, audit);

    const result = await service.tryRoutedInvoker(
      'quick_build',
      'founder-1',
      'Build a valid product plan.',
      'Create a scheduling product.',
    ) as {
      ok: boolean;
      text: string;
      provider: string;
    };

    assert.equal(result.ok, true);
    assert.equal(result.text, '{"ideaTitle":"Runtime governed"}');
    assert.equal(result.provider, 'DEEPSEEK');
    assert.equal(invocations.length, 1);
    assert.equal(invocations[0]?.providerKey, 'deepseek');
    assert.equal(invocations[0]?.maxTokens, 1_234);

    const snapshot = audit.snapshot();
    assert.equal(snapshot.total, 1);
    assert.equal(snapshot.founderRuntime, 1);
    assert.equal(snapshot.bypassed, 0);
    assert.equal(snapshot.governedCoverageRatio, 1);
    assert.equal(snapshot.byCallSite['runtime.quick_build'], 1);
    assert.equal(snapshot.byCallSite['ai_routing.quick_build'], undefined);
  } finally {
    if (previous === undefined) {
      delete process.env.AI_RUNTIME_ENABLED;
    } else {
      process.env.AI_RUNTIME_ENABLED = previous;
    }
  }
});

test('routed invocation preserves the legacy direct path when Founder Runtime is disabled', async () => {
  const previous = process.env.AI_RUNTIME_ENABLED;
  process.env.AI_RUNTIME_ENABLED = 'false';
  try {
    const { audit, runtime } = runtimeHarness();
    const { service, invocations } = builderHarness(runtime, audit);

    await service.tryRoutedInvoker(
      'quick_build',
      'founder-1',
      'Build a valid product plan.',
      'Create a scheduling product.',
    );

    assert.equal(invocations.length, 1);
    assert.equal(invocations[0]?.providerKey, 'deepseek');
    const snapshot = audit.snapshot();
    assert.equal(snapshot.founderRuntime, 0);
    assert.equal(snapshot.bypassed, 1);
    assert.equal(snapshot.byCallSite['ai_routing.quick_build'], 1);
  } finally {
    if (previous === undefined) {
      delete process.env.AI_RUNTIME_ENABLED;
    } else {
      process.env.AI_RUNTIME_ENABLED = previous;
    }
  }
});

test('resolved BYOK completion uses runtime limits without entering managed budget', async () => {
  const previous = process.env.AI_RUNTIME_ENABLED;
  process.env.AI_RUNTIME_ENABLED = 'true';
  try {
    const { audit, runtime } = runtimeHarness();
    const calls: unknown[][] = [];
    const service = Object.create(BuilderService.prototype) as unknown as {
      completionWithProviderViaRuntime: (
        userId: string,
        provider: string,
        apiKey: string,
        system: string,
        userPrompt: string,
        model: string,
        billingSource: 'byok',
      ) => Promise<{
        text: string;
        usage: { promptTokens: number; completionTokens: number } | null;
      } | null>;
    };
    Object.assign(service, {
      founderAiRuntime: runtime,
      providerEgressAudit: audit,
      completionWithProvider: async (...args: unknown[]) => {
        calls.push(args);
        audit.record({
          adapterName: 'builder.legacy-provider',
          provider: 'GLM',
          boundary: 'unscoped',
          callSiteId: 'builder.legacy_completion',
          budgetDomain: 'unattributed_legacy',
        });
        return {
          text: 'review complete',
          usage: { promptTokens: 30, completionTokens: 12 },
        };
      },
    });

    const result = await service.completionWithProviderViaRuntime(
      'founder-1',
      'GLM',
      'secret-never-recorded',
      'system',
      'review this change',
      'glm-5.2',
      'byok',
    );

    assert.equal(result?.text, 'review complete');
    assert.deepEqual(result?.usage, {
      promptTokens: 30,
      completionTokens: 12,
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0]?.[5], 1_234);

    const snapshot = audit.snapshot();
    assert.equal(snapshot.founderRuntime, 1);
    assert.equal(snapshot.bypassed, 0);
    assert.equal(snapshot.recent[0]?.callSiteId, 'runtime.copilot');
    assert.equal(snapshot.recent[0]?.budgetDomain, 'founder_byok');
    assert.equal(
      JSON.stringify(snapshot).includes('secret-never-recorded'),
      false,
    );
  } finally {
    if (previous === undefined) {
      delete process.env.AI_RUNTIME_ENABLED;
    } else {
      process.env.AI_RUNTIME_ENABLED = previous;
    }
  }
});

test('platform promo completion stays in managed runtime budget', async () => {
  const previous = process.env.AI_RUNTIME_ENABLED;
  process.env.AI_RUNTIME_ENABLED = 'true';
  try {
    const { audit, runtime } = runtimeHarness();
    const service = Object.create(BuilderService.prototype) as unknown as {
      completionWithProviderViaRuntime: (
        userId: string,
        provider: string,
        apiKey: string,
        system: string,
        userPrompt: string,
        model: string,
        billingSource: 'platform_promo',
      ) => Promise<unknown>;
    };
    Object.assign(service, {
      founderAiRuntime: runtime,
      providerEgressAudit: audit,
      completionWithProvider: async () => {
        audit.record({
          adapterName: 'builder.legacy-provider',
          provider: 'DEEPSEEK',
        });
        return {
          text: 'managed response',
          usage: { promptTokens: 10, completionTokens: 5 },
        };
      },
    });

    await service.completionWithProviderViaRuntime(
      'founder-1',
      'DEEPSEEK',
      'managed-secret',
      'system',
      'answer',
      'deepseek-v4-flash',
      'platform_promo',
    );

    const snapshot = audit.snapshot();
    assert.equal(snapshot.founderRuntime, 1);
    assert.equal(snapshot.recent[0]?.budgetDomain, 'founder_managed');
  } finally {
    if (previous === undefined) {
      delete process.env.AI_RUNTIME_ENABLED;
    } else {
      process.env.AI_RUNTIME_ENABLED = previous;
    }
  }
});

test('runtime output ceiling reaches the DeepSeek provider request body', async () => {
  const previousEnabled = process.env.AI_RUNTIME_ENABLED;
  const previousFetch = globalThis.fetch;
  process.env.AI_RUNTIME_ENABLED = 'true';
  let requestBody: Record<string, unknown> = {};
  globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
    requestBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    return new Response(
      JSON.stringify({
        choices: [{ message: { content: 'bounded response' } }],
        usage: { prompt_tokens: 8, completion_tokens: 4 },
      }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      },
    );
  }) as typeof fetch;

  try {
    const { audit, runtime } = runtimeHarness();
    const service = Object.create(BuilderService.prototype) as unknown as {
      completionWithProviderViaRuntime: (
        userId: string,
        provider: string,
        apiKey: string,
        system: string,
        userPrompt: string,
        model: string,
        billingSource: 'byok',
      ) => Promise<{ text: string } | null>;
    };
    Object.assign(service, {
      founderAiRuntime: runtime,
      providerEgressAudit: audit,
    });

    const result = await service.completionWithProviderViaRuntime(
      'founder-1',
      'DEEPSEEK',
      'byok-secret',
      'system',
      'answer briefly',
      'deepseek-v4-flash',
      'byok',
    );

    assert.equal(result?.text, 'bounded response');
    assert.equal(requestBody.model, 'deepseek-v4-flash');
    assert.equal(requestBody.max_tokens, 1_234);
    assert.equal(audit.snapshot().recent[0]?.budgetDomain, 'founder_byok');
  } finally {
    globalThis.fetch = previousFetch;
    if (previousEnabled === undefined) {
      delete process.env.AI_RUNTIME_ENABLED;
    } else {
      process.env.AI_RUNTIME_ENABLED = previousEnabled;
    }
  }
});

test('runtime governs DeepSeek stream reads with BYOK budget and output ceiling', async () => {
  const previousEnabled = process.env.AI_RUNTIME_ENABLED;
  const previousFetch = globalThis.fetch;
  process.env.AI_RUNTIME_ENABLED = 'true';
  let requestBody: Record<string, unknown> = {};
  const encoder = new TextEncoder();
  globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
    requestBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"choices":[{"delta":{"content":"streamed"}}]}\n' +
              'data: {"choices":[],"usage":{"prompt_tokens":21,"completion_tokens":9}}\n' +
              'data: [DONE]\n',
          ),
        );
        controller.close();
      },
    });
    return new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    });
  }) as typeof fetch;

  try {
    const { audit, runtime } = runtimeHarness();
    const service = Object.create(BuilderService.prototype) as unknown as {
      completionWithProviderStreamViaRuntime: (
        userId: string,
        provider: string,
        apiKey: string,
        system: string,
        userPrompt: string,
        model: string,
        billingSource: 'byok',
      ) => AsyncGenerator<
        string,
        {
          text: string;
          usage: { promptTokens: number; completionTokens: number } | null;
        } | null
      >;
    };
    Object.assign(service, {
      founderAiRuntime: runtime,
      providerEgressAudit: audit,
    });

    const stream = service.completionWithProviderStreamViaRuntime(
      'founder-1',
      'DEEPSEEK',
      'stream-secret-never-recorded',
      'system',
      'stream answer',
      'deepseek-v4-flash',
      'byok',
    );

    assert.equal(audit.snapshot().total, 0);
    assert.deepEqual(await stream.next(), {
      done: false,
      value: 'streamed',
    });
    const final = await stream.next();
    assert.equal(final.done, true);
    assert.deepEqual(final.value, {
      text: 'streamed',
      usage: { promptTokens: 21, completionTokens: 9 },
    });
    assert.equal(requestBody.max_tokens, 1_234);

    const snapshot = audit.snapshot();
    assert.equal(snapshot.founderRuntime, 1);
    assert.equal(snapshot.bypassed, 0);
    assert.equal(snapshot.recent[0]?.callSiteId, 'runtime.copilot');
    assert.equal(snapshot.recent[0]?.budgetDomain, 'founder_byok');
    assert.equal(
      JSON.stringify(snapshot).includes('stream-secret-never-recorded'),
      false,
    );
  } finally {
    globalThis.fetch = previousFetch;
    if (previousEnabled === undefined) {
      delete process.env.AI_RUNTIME_ENABLED;
    } else {
      process.env.AI_RUNTIME_ENABLED = previousEnabled;
    }
  }
});

test('stopping a governed provider stream cancels the underlying network reader', async () => {
  const previousEnabled = process.env.AI_RUNTIME_ENABLED;
  const previousFetch = globalThis.fetch;
  process.env.AI_RUNTIME_ENABLED = 'true';
  let cancelled = false;
  const encoder = new TextEncoder();
  globalThis.fetch = (async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
          ),
        );
      },
      cancel() {
        cancelled = true;
      },
    });
    return new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    });
  }) as typeof fetch;

  try {
    const { audit, runtime } = runtimeHarness();
    const service = Object.create(BuilderService.prototype) as unknown as {
      completionWithProviderStreamViaRuntime: (
        userId: string,
        provider: string,
        apiKey: string,
        system: string,
        userPrompt: string,
        model: string,
        billingSource: 'platform_promo',
      ) => AsyncGenerator<
        string,
        { text: string; usage: null } | null
      >;
    };
    Object.assign(service, {
      founderAiRuntime: runtime,
      providerEgressAudit: audit,
    });

    const stream = service.completionWithProviderStreamViaRuntime(
      'founder-1',
      'DEEPSEEK',
      'managed-stream-secret',
      'system',
      'long answer',
      'deepseek-v4-flash',
      'platform_promo',
    );

    assert.equal((await stream.next()).value, 'partial');
    await stream.return(null);
    assert.equal(cancelled, true);
    assert.equal(
      audit.snapshot().recent[0]?.budgetDomain,
      'founder_managed',
    );
  } finally {
    globalThis.fetch = previousFetch;
    if (previousEnabled === undefined) {
      delete process.env.AI_RUNTIME_ENABLED;
    } else {
      process.env.AI_RUNTIME_ENABLED = previousEnabled;
    }
  }
});
