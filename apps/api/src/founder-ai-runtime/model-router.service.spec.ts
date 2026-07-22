import assert from 'node:assert/strict';
import test from 'node:test';
import { ModelRouterService } from './model-router.service';

const router = new ModelRouterService({
  getSyncConfig: () => ({
    twoModelRoutingEnabled: true,
    deepseekFastModel: 'deepseek-v4-flash',
    deepseekCodingModel: 'deepseek-v4-pro',
    glmFastModel: 'glm-4-flash',
    glmCodingModel: 'glm-5.2',
    defaultMode: 'automatic',
  }),
} as never);

function request(userPrompt: string) {
  return {
    userId: 'user-test',
    system: 'You are Founder AI.',
    userPrompt,
    section: 'copilot' as const,
  };
}

test('managed model router uses DeepSeek Flash for routine work', () => {
  const route = router.route(request('summarize this status'));
  assert.equal(route.providerKey, 'deepseek');
  assert.equal(route.model, 'deepseek-v4-flash');
  assert.equal(route.tier, 'fast');
});

test('managed model router uses DeepSeek Pro for code and reasoning', () => {
  for (const prompt of ['implement this TypeScript change', 'analyze the architecture']) {
    const route = router.route(request(prompt));
    assert.equal(route.providerKey, 'deepseek');
    assert.equal(route.model, 'deepseek-v4-pro');
    assert.ok(route.tier === 'code' || route.tier === 'reasoning');
  }
});

test('fallback stays within managed DeepSeek and escalates to Pro', () => {
  const route = router.getFallbackRoute(request('summarize this status'));
  assert.equal(route.providerKey, 'deepseek');
  assert.equal(route.model, 'deepseek-v4-pro');
  assert.equal(route.tier, 'reasoning');
});
