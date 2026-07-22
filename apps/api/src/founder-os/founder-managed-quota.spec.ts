import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  chargeForManagedReservation,
  reconcileProviderUsage,
} from './founder-managed-quota';

describe('Founder managed quota accounting', () => {
  it('counts active and uncertain reservations at their full reserved amount', () => {
    for (const status of ['RESERVED', 'UNCERTAIN'] as const) {
      assert.equal(
        chargeForManagedReservation({
          status,
          reservedWeightedUnits: 12_500,
          actualWeightedUnits: null,
        }),
        12_500,
      );
    }
  });

  it('uses reconciled actual units and releases failed requests', () => {
    assert.equal(
      chargeForManagedReservation({
        status: 'RECONCILED',
        reservedWeightedUnits: 12_500,
        actualWeightedUnits: 1_250,
      }),
      1_250,
    );
    assert.equal(
      chargeForManagedReservation({
        status: 'RELEASED',
        reservedWeightedUnits: 12_500,
        actualWeightedUnits: null,
      }),
      0,
    );
  });

  it('weights cache hits and reasoning from provider usage', () => {
    const result = reconcileProviderUsage(
      {
        prompt_tokens: 1_000,
        prompt_cache_hit_tokens: 600,
        prompt_cache_miss_tokens: 400,
        completion_tokens: 200,
        completion_tokens_details: { reasoning_tokens: 50 },
      },
      20_000,
    );
    assert.deepEqual(result, {
      inputTokens: 400,
      cachedInputTokens: 600,
      outputTokens: 150,
      reasoningTokens: 50,
      weightedUnits: 1_150,
      providerMeasured: true,
    });
  });

  it('keeps the full reservation when the provider omits usage', () => {
    assert.equal(reconcileProviderUsage(undefined, 9_001).weightedUnits, 9_001);
  });
});
