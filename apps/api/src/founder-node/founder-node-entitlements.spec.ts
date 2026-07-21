import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { toFounderIdeEntitlements } from './founder-node-entitlements.js';

describe('toFounderIdeEntitlements', () => {
  it('maps the paired-node allowance without inventing paid-plan limits', () => {
    const result = toFounderIdeEntitlements({
      enabled: true,
      eligible: true,
      founderRegistered: true,
      promoStartedAt: '2026-07-01T00:00:00.000Z',
      expiresAt: '2026-07-31T00:00:00.000Z',
      daysRemaining: 10,
      tokenCap: 5_000_000,
      tokensUsed: 750_000,
      tokensRemaining: 4_250_000,
      exhausted: false,
      message: 'Founder Free is active.',
      providers: ['DEEPSEEK'],
    });

    assert.deepEqual(result, {
      plan: 'free',
      managedTokens: {
        cap: 5_000_000,
        used: 750_000,
        remaining: 4_250_000,
        eligible: true,
        resetsOrExpiresAt: '2026-07-31T00:00:00.000Z',
        daysRemaining: 10,
      },
      personalProviders: {
        used: null,
        limit: null,
        localModelsCountTowardLimit: false,
      },
      message: 'Founder Free is active.',
    });
  });
});
