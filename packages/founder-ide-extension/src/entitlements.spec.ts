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
