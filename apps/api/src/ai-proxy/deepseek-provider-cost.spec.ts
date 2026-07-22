import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  calculateDeepseekCostUsd,
  DEEPSEEK_PRICE_VERSION,
  estimateDeepseekInputSavingsUsd,
} from './deepseek-provider-cost';

describe('DeepSeek provider cost', () => {
  it('prices Flash cache hits, misses, and output separately', () => {
    const result = calculateDeepseekCostUsd('deepseek-v4-flash', {
      prompt_tokens: 180_000,
      prompt_cache_hit_tokens: 150_000,
      prompt_cache_miss_tokens: 30_000,
      completion_tokens: 20_000,
    });
    assert.ok(result);
    assert.equal(result.priceVersion, DEEPSEEK_PRICE_VERSION);
    assert.equal(result.cachedInputTokens, 150_000);
    assert.equal(result.uncachedInputTokens, 30_000);
    assert.ok(Math.abs(result.costUsd - 0.01022) < 1e-10);
  });

  it('uses the official Pro rates', () => {
    const result = calculateDeepseekCostUsd('deepseek-v4-pro', {
      prompt_tokens: 10_000,
      completion_tokens: 3_000,
    });
    assert.ok(result);
    assert.ok(Math.abs(result.costUsd - 0.00696) < 1e-10);
  });

  it('returns null for an unversioned model or missing provider usage', () => {
    assert.equal(calculateDeepseekCostUsd('deepseek-chat', {}), null);
    assert.equal(calculateDeepseekCostUsd('deepseek-v4-flash', null), null);
  });

  it('labels an input-only full-context comparison as estimated', () => {
    assert.deepEqual(estimateDeepseekInputSavingsUsd('deepseek-v4-flash', 50_000), {
      measurement: 'estimated',
      baseline: 'same-request-full-context-uncached-input',
      currency: 'USD',
      priceVersion: DEEPSEEK_PRICE_VERSION,
      avoidedInputTokens: 50_000,
      avoidedUsd: 0.007,
    });
  });
});
