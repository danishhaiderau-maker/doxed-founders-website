import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { FounderCoordinationCloud } from './agent-coordination-cloud';

const credentials = { apiBaseUrl: 'https://example.test', nodeId: 'node-1', nodeToken: 'secret' };
const card = { clientTaskId: 'local-1', workspaceKey: 'repo:abc', title: 'Build settings' };

describe('Founder coordination cloud client', () => {
  it('uses paired-node authentication and never puts credentials in the URL', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const cloud = new FounderCoordinationCloud(async (url, init) => {
      requests.push({ url: String(url), init });
      return new Response(JSON.stringify({
        id: 'server-1', ownerUserId: 'user-1', status: 'ACTIVE',
        heartbeatAt: new Date().toISOString(), expiresAt: new Date().toISOString(), claims: [], ...card,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    cloud.begin(credentials, card);
    await cloud.heartbeat('local-1');
    assert.match(requests[0]!.url, /\/api\/founder-node\/coordination\/tasks$/);
    assert.doesNotMatch(requests[0]!.url, /secret|node-1/);
    assert.equal((requests[0]!.init?.headers as Record<string, string>).Authorization, 'FounderNode node-1:secret');
  });

  it('returns a hard conflict from a competing server claim', async () => {
    let request = 0;
    const cloud = new FounderCoordinationCloud(async () => {
      request += 1;
      if (request === 1) {
        return new Response(JSON.stringify({
          id: 'server-1', ownerUserId: 'user-1', status: 'ACTIVE',
          heartbeatAt: new Date().toISOString(), expiresAt: new Date().toISOString(), claims: [], ...card,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({
        error: 'path_claimed', ownerTaskId: 'server-2', ownerTitle: 'Other task',
      }), { status: 409, headers: { 'Content-Type': 'application/json' } });
    });
    cloud.begin(credentials, card);
    const result = await cloud.claim('local-1', 'src/settings.ts');
    assert.equal(result.ok, false);
    if (!result.ok) assert.equal(result.ownerTitle, 'Other task');
  });

  it('keeps offline/local work available when cloud sync is unavailable', async () => {
    const cloud = new FounderCoordinationCloud(async () => { throw new Error('offline'); });
    cloud.begin(credentials, card);
    assert.deepEqual(await cloud.claim('local-1', 'src/settings.ts'), { ok: true, synced: false });
  });
});
