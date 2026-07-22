import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  FOUNDER_FREE_MANAGED_TOKEN_CAP,
  fetchFounderIdeEntitlements,
} from './entitlements.js';

describe('fetchFounderIdeEntitlements', () => {
  it('uses the paired Node credential without exposing it in the URL', async () => {
    let requestedUrl = '';
    let requestedAuthorization = '';
    const result = await fetchFounderIdeEntitlements(
      {
        apiBaseUrl: 'https://doxxedcrypto.digital/',
        nodeId: 'node-1',
        nodeToken: 'secret-token',
      },
      (async (input, init) => {
        requestedUrl = String(input);
        requestedAuthorization = String(
          (init?.headers as Record<string, string> | undefined)?.Authorization,
        );
        return new Response(
          JSON.stringify({
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
              used: 20,
              remaining: 199_980,
              eligible: true,
              resetsOrExpiresAt: null,
              daysRemaining: null,
            },
            personalProviders: {
              used: null,
              limit: null,
              localModelsCountTowardLimit: false,
            },
            message: null,
          }),
          { status: 200 },
        );
      }) as typeof fetch,
    );

    assert.equal(
      requestedUrl,
      'https://doxxedcrypto.digital/api/founder-node/ide-entitlements',
    );
    assert.equal(requestedAuthorization, 'FounderNode node-1:secret-token');
    assert.equal(result.source, 'live');
    assert.equal(result.value.managedTokens.remaining, 199_980);
  });

  it('accepts Builder and Team plan names returned by the current API', async () => {
    for (const plan of ['builder', 'team'] as const) {
      const result = await fetchFounderIdeEntitlements(
        { apiBaseUrl: 'https://example.test', nodeId: 'node-1', nodeToken: 'secret-token' },
        (async () => new Response(JSON.stringify({
          plan,
          priceCentsMonthly: plan === 'builder' ? 3_500 : null,
          team: plan === 'team' ? { id: 'team-1', name: 'Studio', role: 'owner' } : null,
          features: { coordination: true, remoteControl: true, rolesAndAudit: plan === 'team' },
          managedTokens: {
            unit: 'weighted_tokens', weightsVersion: 'founder-wtu-v1', cap: 5_000_000,
            used: 100, remaining: 4_999_900, eligible: true,
            resetsOrExpiresAt: null, daysRemaining: null,
          },
          personalProviders: { used: null, limit: null, localModelsCountTowardLimit: false },
          message: null,
        }), { status: 200 })) as typeof fetch,
      );
      assert.equal(result.source, 'live');
      assert.equal(result.value.plan, plan);
    }
  });

  it('returns a truthful signed-out state without a network request', async () => {
    let called = false;
    const result = await fetchFounderIdeEntitlements(null, (async () => {
      called = true;
      throw new Error('unexpected');
    }) as typeof fetch);

    assert.equal(called, false);
    assert.equal(result.source, 'signed-out');
    assert.equal(result.value.managedTokens.cap, FOUNDER_FREE_MANAGED_TOKEN_CAP);
    assert.equal(result.value.managedTokens.eligible, false);
  });
});
