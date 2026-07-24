import assert from 'node:assert/strict';
import test from 'node:test';
import { transcribeManagedVoiceRequest } from './managed-voice-request';

test('managed voice uses Founder Node authentication without exposing provider keys', async () => {
  let requestUrl = '';
  let authorization = '';
  const result = await transcribeManagedVoiceRequest(
    { audioBase64: Buffer.alloc(44).toString('base64') },
    {
      apiBaseUrl: 'https://founder.test',
      authorization: 'FounderNode node-1:secret-token',
    },
    {
      platform: 'linux',
      fetchImpl: (async (url, init) => {
        requestUrl = String(url);
        authorization = String(
          (init?.headers as Record<string, string>).Authorization,
        );
        return new Response(
          JSON.stringify({
            text: 'review the release',
            provider: 'glm',
            model: 'glm-asr-2512',
            route: 'founder-managed-speech',
          }),
          { status: 200 },
        );
      }) as typeof fetch,
    },
  );

  assert.equal(
    requestUrl,
    'https://founder.test/api/v1/audio/transcriptions',
  );
  assert.equal(authorization, 'FounderNode node-1:secret-token');
  assert.equal(result.text, 'review the release');
});

test('managed voice uses the proxy-aware native transport on Windows', async () => {
  let requestUrl = '';
  let authorization = '';
  let audioBytes = 0;
  const result = await transcribeManagedVoiceRequest(
    { audioBase64: Buffer.alloc(48).toString('base64') },
    {
      apiBaseUrl: 'https://founder.test/',
      authorization: 'FounderNode node-2:secret-token',
    },
    {
      platform: 'win32',
      fetchImpl: (async () => {
        throw new Error('Node fetch must not run on Windows.');
      }) as typeof fetch,
      nativeFetchImpl: async (url, audio, auth) => {
        requestUrl = url;
        authorization = auth;
        audioBytes = audio.byteLength;
        return new Response(
          JSON.stringify({
            text: 'ship the release',
            provider: 'glm',
            model: 'glm-asr-2512',
            route: 'founder-managed-speech',
          }),
          { status: 200 },
        );
      },
    },
  );

  assert.equal(requestUrl, 'https://founder.test/api/v1/audio/transcriptions');
  assert.equal(authorization, 'FounderNode node-2:secret-token');
  assert.equal(audioBytes, 48);
  assert.equal(result.text, 'ship the release');
});

test('managed voice keeps native transport failures secret-safe', async () => {
  await assert.rejects(
    transcribeManagedVoiceRequest(
      { audioBase64: Buffer.alloc(48).toString('base64') },
      {
        apiBaseUrl: 'https://founder.test',
        authorization: 'FounderNode node-3:must-not-leak',
      },
      {
        platform: 'win32',
        nativeFetchImpl: async () => {
          throw new Error('network failure with FounderNode node-3:must-not-leak');
        },
      },
    ),
    (error: unknown) =>
      error instanceof Error &&
      error.message ===
        'Founder managed voice could not reach its speech service. Your typed text is unchanged.' &&
      !error.message.includes('must-not-leak'),
  );
});
