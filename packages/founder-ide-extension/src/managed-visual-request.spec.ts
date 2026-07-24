import assert from 'node:assert/strict';
import test from 'node:test';
import { describeManagedVisualRequest } from './managed-visual-request';

const screenshot = {
  name: 'annotated.png',
  mimeType: 'image/png',
  dataBase64: Buffer.alloc(48).toString('base64'),
};

test('managed vision uses Founder Node auth without exposing the provider key', async () => {
  let requestUrl = '';
  let authorization = '';
  let body: { attachments?: unknown[] } = {};
  const result = await describeManagedVisualRequest(
    { attachments: [screenshot] },
    {
      apiBaseUrl: 'https://founder.test/',
      authorization: 'FounderNode node-1:secret-token',
    },
    {
      platform: 'linux',
      fetchImpl: (async (url, init) => {
        requestUrl = String(url);
        authorization = String(
          (init?.headers as Record<string, string>).Authorization,
        );
        body = JSON.parse(String(init?.body)) as { attachments?: unknown[] };
        return new Response(
          JSON.stringify({
            descriptions: [
              {
                name: 'annotated.png',
                description: 'A red circle marks the navigation rail.',
              },
            ],
            provider: 'glm',
            model: 'glm-4.6v-flash',
            route: 'founder-managed-vision',
          }),
          { status: 200 },
        );
      }) as typeof fetch,
    },
  );

  assert.equal(requestUrl, 'https://founder.test/api/v1/images/descriptions');
  assert.equal(authorization, 'FounderNode node-1:secret-token');
  assert.equal(body.attachments?.length, 1);
  assert.equal(result.descriptions[0].name, 'annotated.png');
});

test('managed vision uses the native Windows transport', async () => {
  let requestBytes = 0;
  const result = await describeManagedVisualRequest(
    { attachments: [screenshot] },
    {
      apiBaseUrl: 'https://founder.test',
      authorization: 'FounderNode node-2:secret-token',
    },
    {
      platform: 'win32',
      fetchImpl: (async () => {
        throw new Error('Node fetch must not run on Windows.');
      }) as typeof fetch,
      nativeFetchImpl: async (_url, body) => {
        requestBytes = body.byteLength;
        return new Response(
          JSON.stringify({
            descriptions: [
              {
                name: 'annotated.png',
                description: 'A screenshot of Founder IDE.',
              },
            ],
          }),
          { status: 200 },
        );
      },
    },
  );

  assert.ok(requestBytes > screenshot.dataBase64.length);
  assert.equal(result.model, 'glm-4.6v-flash');
});

test('managed vision rejects unsupported and oversized attachments before networking', async () => {
  let requested = false;
  await assert.rejects(
    describeManagedVisualRequest(
      {
        attachments: [
          { ...screenshot, mimeType: 'image/gif' },
        ],
      },
      {
        apiBaseUrl: 'https://founder.test',
        authorization: 'FounderNode node-3:secret-token',
      },
      {
        platform: 'linux',
        fetchImpl: (async () => {
          requested = true;
          throw new Error('must not run');
        }) as typeof fetch,
      },
    ),
    /PNG, JPEG, and WebP/,
  );
  assert.equal(requested, false);
});
