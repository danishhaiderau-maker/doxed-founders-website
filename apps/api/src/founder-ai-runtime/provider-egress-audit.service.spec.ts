import assert from 'node:assert/strict';
import test from 'node:test';
import { ProviderEgressAuditService } from './provider-egress-audit.service';
import {
  PROVIDER_EGRESS_CALL_SITE_IDS,
  routedCallSiteForSection,
  runtimeCallSiteForSection,
} from './provider-egress-audit.types';
import { FounderAiRuntimeService } from './founder-ai-runtime.service';

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
  assert.equal(routedCallSiteForSection('new-unregistered-section'), 'ai_routing.other');
  assert.equal(new Set(PROVIDER_EGRESS_CALL_SITE_IDS).size, PROVIDER_EGRESS_CALL_SITE_IDS.length);
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
