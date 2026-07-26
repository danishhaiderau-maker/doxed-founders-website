import assert from 'node:assert/strict';
import test from 'node:test';
import { ProviderEgressAuditService } from './provider-egress-audit.service';
import {
  PROVIDER_EGRESS_CALL_SITE_IDS,
  routedCallSiteForSection,
  runtimeCallSiteForSection,
} from './provider-egress-audit.types';
import { FounderAiRuntimeService } from './founder-ai-runtime.service';
import {
  isFounderAiRuntimeEnabled,
  isProviderEgressEnforcementStrict,
} from './founder-ai-runtime.config';

test('provider egress audit carries stable runtime context across async work', async () => {
  const audit = new ProviderEgressAuditService();

  await audit.runWithContext(
    {
      boundary: 'founder_ai_runtime',
      callSiteId: 'runtime.copilot',
      budgetDomain: 'founder_managed',
      runtimeExecutionId: 'runtime-1',
    },
    async () => {
      await Promise.resolve();
      audit.record({
        adapterName: 'ai-invoker.openai-compatible',
        provider: 'deepseek',
      });
    },
  );

  const snapshot = audit.snapshot();
  assert.equal(snapshot.total, 1);
  assert.equal(snapshot.governed, 1);
  assert.equal(snapshot.founderRuntime, 1);
  assert.equal(snapshot.bypassed, 0);
  assert.equal(snapshot.governedCoverageRatio, 1);
  assert.equal(snapshot.founderRuntimeCoverageRatio, 1);
  assert.deepEqual(snapshot.recent[0], {
    adapterName: 'ai-invoker.openai-compatible',
    provider: 'deepseek',
    boundary: 'founder_ai_runtime',
    callSiteId: 'runtime.copilot',
    budgetDomain: 'founder_managed',
    runtimeExecutionId: 'runtime-1',
    timestamp: snapshot.recent[0]?.timestamp,
  });
});

test('approved exceptions do not inflate or reduce Founder runtime coverage', () => {
  const audit = new ProviderEgressAuditService();
  audit.record({
    adapterName: 'builder.key-verification',
    provider: 'deepseek',
    boundary: 'approved_exception',
    callSiteId: 'builder.key_verification',
    budgetDomain: 'provider_verification',
    runtimeExecutionId: 'verify-1',
  });
  audit.record({
    adapterName: 'builder.legacy-provider',
    provider: 'deepseek',
    boundary: 'unscoped',
    callSiteId: 'builder.legacy_completion',
    budgetDomain: 'unattributed_legacy',
    runtimeExecutionId: 'legacy-1',
  });

  const snapshot = audit.snapshot();
  assert.equal(snapshot.total, 2);
  assert.equal(snapshot.approvedExceptions, 1);
  assert.equal(snapshot.bypassed, 1);
  assert.equal(snapshot.governedCoverageRatio, 0);
  assert.equal(snapshot.founderRuntimeCoverageRatio, 0);
  assert.deepEqual(snapshot.unscopedCallSites, ['builder.legacy_completion']);
});

test('managed IDE and auxiliary calls remain visible without becoming bypasses', async () => {
  const audit = new ProviderEgressAuditService();
  await audit.runWithContext(
    {
      boundary: 'ai_proxy_runtime',
      callSiteId: 'ai_proxy.chat',
      budgetDomain: 'founder_managed_chat',
    },
    async () => {
      audit.record({
        adapterName: 'ai-proxy.openai-compatible',
        provider: 'deepseek',
      });
    },
  );
  await audit.runWithContext(
    {
      boundary: 'managed_auxiliary',
      callSiteId: 'ai_proxy.visual',
      budgetDomain: 'founder_managed_vision',
    },
    async () => {
      audit.record({
        adapterName: 'ai-proxy.glm-vision',
        provider: 'glm',
      });
    },
  );

  const snapshot = audit.snapshot();
  assert.equal(snapshot.ideProxyRuntime, 1);
  assert.equal(snapshot.managedAuxiliary, 1);
  assert.equal(snapshot.governed, 2);
  assert.equal(snapshot.bypassed, 0);
  assert.equal(snapshot.governedCoverageRatio, 1);
  assert.equal(snapshot.founderRuntimeCoverageRatio, 0);
});

test('call-site registry maps every runtime section and rejects ad-hoc IDs at compile time', () => {
  assert.equal(runtimeCallSiteForSection('quick_build'), 'runtime.quick_build');
  assert.equal(routedCallSiteForSection('quick_build'), 'ai_routing.quick_build');
  assert.throws(
    () => routedCallSiteForSection('new-unregistered-section'),
    /Unregistered AI routing section/,
  );
  assert.equal(new Set(PROVIDER_EGRESS_CALL_SITE_IDS).size, PROVIDER_EGRESS_CALL_SITE_IDS.length);
});

test('Founder AI runtime defaults on and requires an explicit false rollback', () => {
  assert.equal(isFounderAiRuntimeEnabled({}), true);
  assert.equal(isFounderAiRuntimeEnabled({ AI_RUNTIME_ENABLED: 'true' }), true);
  assert.equal(isFounderAiRuntimeEnabled({ AI_RUNTIME_ENABLED: 'unexpected' }), true);
  assert.equal(isFounderAiRuntimeEnabled({ AI_RUNTIME_ENABLED: 'false' }), false);
});

test('provider egress strict mode is explicit', () => {
  assert.equal(isProviderEgressEnforcementStrict({}), false);
  assert.equal(
    isProviderEgressEnforcementStrict({
      PROVIDER_EGRESS_ENFORCEMENT: 'strict',
    }),
    true,
  );
});

test('strict provider egress blocks an unscoped call before network dispatch', () => {
  const previous = process.env.PROVIDER_EGRESS_ENFORCEMENT;
  process.env.PROVIDER_EGRESS_ENFORCEMENT = 'strict';
  try {
    const audit = new ProviderEgressAuditService();
    assert.throws(
      () =>
        audit.record({
          adapterName: 'builder.legacy-provider',
          provider: 'deepseek',
          boundary: 'unscoped',
          callSiteId: 'builder.legacy_completion',
          budgetDomain: 'unattributed_legacy',
        }),
      /Route this call through FounderAiRuntimeService/,
    );
    assert.deepEqual(audit.snapshot().unscopedCallSites, [
      'builder.legacy_completion',
    ]);
  } finally {
    if (previous === undefined) {
      delete process.env.PROVIDER_EGRESS_ENFORCEMENT;
    } else {
      process.env.PROVIDER_EGRESS_ENFORCEMENT = previous;
    }
  }
});

test('strict provider egress permits explicit approved exceptions', () => {
  const previous = process.env.PROVIDER_EGRESS_ENFORCEMENT;
  process.env.PROVIDER_EGRESS_ENFORCEMENT = 'strict';
  try {
    const audit = new ProviderEgressAuditService();
    assert.doesNotThrow(() =>
      audit.record({
        adapterName: 'builder.key-verification',
        provider: 'deepseek',
        boundary: 'approved_exception',
        callSiteId: 'builder.key_verification',
        budgetDomain: 'provider_verification',
      }),
    );
    assert.equal(audit.snapshot().approvedExceptions, 1);
  } finally {
    if (previous === undefined) {
      delete process.env.PROVIDER_EGRESS_ENFORCEMENT;
    } else {
      process.env.PROVIDER_EGRESS_ENFORCEMENT = previous;
    }
  }
});

test('FounderAiRuntime.complete owns provider egress during its invoke callback', async () => {
  const audit = new ProviderEgressAuditService();
  const runtime = new FounderAiRuntimeService(
    {} as never,
    {
      route: () => ({
        intent: 'simple_qa',
        providerKey: 'deepseek',
        model: 'deepseek-v4-flash',
        tier: 'fast',
      }),
    } as never,
    {
      prepareRequest: (request: unknown) => request,
      maxOutputTokens: () => 1_024,
    } as never,
    audit,
  );

  const result = await runtime.complete(
    {
      userId: 'user-1',
      system: 'system',
      userPrompt: 'hello',
      section: 'quick_build',
      skipCache: true,
    },
    async () => {
      audit.record({
        adapterName: 'ai-invoker.openai-compatible',
        provider: 'deepseek',
      });
      return {
        ok: true,
        text: 'done',
        provider: 'deepseek',
        model: 'deepseek-v4-flash',
      };
    },
  );

  assert.equal(result.text, 'done');
  const snapshot = audit.snapshot();
  assert.equal(snapshot.founderRuntime, 1);
  assert.equal(snapshot.byCallSite['runtime.quick_build'], 1);
});

test('provider egress audit governs every lazy stream operation with one execution identity', async () => {
  const audit = new ProviderEgressAuditService();
  async function* source(): AsyncGenerator<string, string> {
    try {
      audit.record({
        adapterName: 'builder.legacy-stream',
        provider: 'deepseek',
      });
      yield 'first';
      audit.record({
        adapterName: 'builder.legacy-stream',
        provider: 'deepseek',
      });
      yield 'second';
      return 'complete';
    } finally {
      audit.record({
        adapterName: 'builder.stream-cleanup',
        provider: 'deepseek',
      });
    }
  }

  const stream = audit.wrapAsyncGeneratorWithContext(
    {
      boundary: 'founder_ai_runtime',
      callSiteId: 'runtime.copilot',
      budgetDomain: 'founder_byok',
      runtimeExecutionId: 'stream-runtime-1',
    },
    source(),
  );

  assert.equal(audit.snapshot().total, 0);
  assert.deepEqual(await stream.next(), { done: false, value: 'first' });
  assert.deepEqual(await stream.next(), { done: false, value: 'second' });
  assert.deepEqual(await stream.return('cancelled'), {
    done: true,
    value: 'cancelled',
  });

  const snapshot = audit.snapshot();
  assert.equal(snapshot.total, 3);
  assert.equal(snapshot.founderRuntime, 3);
  assert.equal(snapshot.bypassed, 0);
  assert.deepEqual(
    new Set(snapshot.recent.map((event) => event.runtimeExecutionId)),
    new Set(['stream-runtime-1']),
  );
  assert.ok(
    snapshot.recent.every(
      (event) =>
        event.callSiteId === 'runtime.copilot' &&
        event.budgetDomain === 'founder_byok',
    ),
  );
});

test('FounderAiRuntime.stream applies prepared context and output ceiling lazily', async () => {
  const previous = process.env.AI_RUNTIME_ENABLED;
  process.env.AI_RUNTIME_ENABLED = 'true';
  const audit = new ProviderEgressAuditService();
  try {
    const runtime = new FounderAiRuntimeService(
      {} as never,
      {
        route: () => ({
          intent: 'code',
          providerKey: 'deepseek',
          model: 'deepseek-v4-pro',
          tier: 'code',
        }),
      } as never,
      {
        prepareRequest: (request: { system: string }) => ({
          ...request,
          system: 'prepared-system',
        }),
        maxOutputTokens: () => 777,
      } as never,
      audit,
    );
    let invoked = false;

    const stream = runtime.stream(
      {
        userId: 'user-1',
        system: 'unpruned-system',
        userPrompt: 'review',
        section: 'copilot',
      },
      (_route, context) =>
        (async function* () {
          invoked = true;
          assert.equal(context.request.system, 'prepared-system');
          assert.equal(context.maxOutputTokens, 777);
          audit.record({
            adapterName: 'builder.legacy-stream',
            provider: 'deepseek',
          });
          yield 'delta';
          return 'done';
        })(),
      { budgetDomain: 'founder_managed' },
    );

    assert.equal(invoked, false);
    assert.deepEqual(await stream.next(), { done: false, value: 'delta' });
    assert.equal(invoked, true);
    assert.deepEqual(await stream.next(), { done: true, value: 'done' });
    assert.equal(
      audit.snapshot().recent[0]?.budgetDomain,
      'founder_managed',
    );
  } finally {
    if (previous === undefined) {
      delete process.env.AI_RUNTIME_ENABLED;
    } else {
      process.env.AI_RUNTIME_ENABLED = previous;
    }
  }
});
