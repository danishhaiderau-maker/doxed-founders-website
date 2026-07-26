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
