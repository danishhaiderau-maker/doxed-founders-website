import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { afterEach, describe, it } from 'node:test';
import { installFingerprint, requestDeviceCode } from '../src/device-code-flow';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe('Founder Node device-code request', () => {
  it('derives the same readable non-secret install fingerprint for local comparison', () => {
    const installId = 'install-123';
    const expected = createHash('sha256')
      .update(installId)
      .digest('hex')
      .slice(0, 12)
      .toUpperCase()
      .match(/.{1,4}/g)
      ?.join('-');

    assert.equal(installFingerprint(installId), expected);
    assert.match(installFingerprint(installId), /^[0-9A-F]{4}(?:-[0-9A-F]{4}){2}$/);
    assert.equal(installFingerprint(installId).includes(installId), false);
  });

  it('sends bounded device identity from the main process with the secret in the body', async () => {
    let observedUrl = '';
    let observedBody: Record<string, unknown> = {};
    globalThis.fetch = (async (input, init) => {
      observedUrl = String(input);
      observedBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
      return new Response(JSON.stringify({
        deviceCode: 'd'.repeat(64),
        userCode: 'ABCD-2345',
        verificationUri: 'https://example.test/founder-id/authorize',
        expiresAt: '2026-07-28T04:00:00.000Z',
        interval: 5,
      }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }) as typeof fetch;

    const ipcSecret = 'a'.repeat(64);
    await requestDeviceCode(
      'https://api.example.test',
      'install-123',
      ipcSecret,
      {
        deviceLabel: 'Danish Laptop',
        platform: 'win32',
        appVersion: '1.0.0',
      },
    );

    assert.equal(observedUrl, 'https://api.example.test/api/founder-node/device-code');
    assert.deepEqual(observedBody, {
      installId: 'install-123',
      ipcSecret,
      deviceLabel: 'Danish Laptop',
      platform: 'win32',
      appVersion: '1.0.0',
    });
    assert.equal(observedUrl.includes(ipcSecret), false);
  });
});
