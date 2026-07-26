import assert from 'node:assert/strict';
import test from 'node:test';
import { ModelRouterService } from './model-router.service';

function createRouter(): ModelRouterService {
  return new ModelRouterService({
    getSyncConfig: () => ({
      twoModelRoutingEnabled: true,
      deepseekFastModel: 'deepseek-v4-flash',
      deepseekCodingModel: 'deepseek-v4-pro',
      glmFastModel: 'glm-4-flash',
      glmCodingModel: 'glm-5.2',
      defaultMode: 'automatic',
    }),
    resolveRouteProviders: (route: unknown) => route,
  } as never);
}

test('managed simple work stays on DeepSeek V4 Flash', () => {
  const route = createRouter().route({
    userId: 'founder-1',
    system: 'You are Founder AI.',
    userPrompt: 'hello',
    section: 'copilot',
  });

  assert.deepEqual(route, {
    intent: 'simple_qa',
    providerKey: 'deepseek',
    model: 'deepseek-v4-flash',
    tier: 'fast',
  });
});

test('managed code and reasoning stay on DeepSeek V4 Pro despite legacy GLM settings', () => {
  const router = createRouter();

  assert.deepEqual(
    router.route({
      userId: 'founder-1',
      system: 'You are Founder AI.',
      userPrompt: 'Implement the TypeScript API route',
      section: 'copilot',
    }),
    {
      intent: 'code',
      providerKey: 'deepseek',
      model: 'deepseek-v4-pro',
      tier: 'code',
    },
  );
  assert.deepEqual(
    router.route({
      userId: 'founder-1',
      system: 'You are Founder AI.',
      userPrompt: 'Compare the architecture tradeoffs',
      section: 'copilot',
    }),
    {
      intent: 'reasoning',
      providerKey: 'deepseek',
      model: 'deepseek-v4-pro',
      tier: 'reasoning',
    },
  );
});

test('managed fallback never becomes a platform GLM call', () => {
  const route = createRouter().getFallbackRoute({
    userId: 'founder-1',
    system: 'You are Founder AI.',
    userPrompt: 'Plan the architecture',
    section: 'platform_brain',
  });

  assert.equal(route.providerKey, 'deepseek');
  assert.equal(route.model, 'deepseek-v4-pro');
  assert.equal(route.tier, 'reasoning');
});
