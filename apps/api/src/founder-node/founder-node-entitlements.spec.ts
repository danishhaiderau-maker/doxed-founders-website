import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { toFounderIdeEntitlements } from './founder-node-entitlements.js';

describe('toFounderIdeEntitlements', () => {
  it('maps the paired-node allowance without inventing paid-plan limits', () => {
    const result = toFounderIdeEntitlements({
      plan: 'free',
      priceCentsMonthly: 0,
      teamId: null,
      teamName: null,
      teamRole: null,
      coordination: false,
      remoteControl: false,
      rolesAndAudit: false,
      unit: 'weighted_tokens',
      weightsVersion: 'founder-wtu-v1',
      enabled: true,
      eligible: true,
      founderRegistered: true,
      promoStartedAt: '2026-07-01T00:00:00.000Z',
      expiresAt: '2026-07-31T00:00:00.000Z',
      daysRemaining: 10,
      tokenCap: 200_000,
      tokensUsed: 75_000,
      tokensRemaining: 125_000,
      exhausted: false,
      message: 'Founder Free is active.',
      providers: ['DEEPSEEK'],
    });

    assert.deepEqual(result, {
      plan: 'free',
      priceCentsMonthly: 0,
      team: null,
      features: {
        coordination: false,
        remoteControl: false,
        rolesAndAudit: false,
      },
      managedTokens: {
        unit: 'weighted_tokens',
        weightsVersion: 'founder-wtu-v1',
        cap: 200_000,
        used: 75_000,
        remaining: 125_000,
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
