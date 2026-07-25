import assert from 'node:assert/strict';
import test from 'node:test';
import {
  BadGatewayException,
  BadRequestException,
  ServiceUnavailableException,
} from '@nestjs/common';
import { AiProxyVisualService } from './ai-proxy-visual.service';

const PNG_BASE64 = Buffer.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
]).toString('base64');

function createService(options?: { key?: string | null }) {
  const writes: unknown[] = [];
  const service = new AiProxyVisualService(
    {
      getDecryptedPlatformGlmVisionKey: async () =>
        options?.key === undefined ? 'platform-vision-secret' : options.key,
    } as never,
    {
      aiTokenUsageLog: {
        create: async (input: unknown) => {
          writes.push(input);
          return input;
        },
      },
    } as never,
  );
  return { service, writes };
}

test('managed vision fails closed without a platform vision key', async () => {
  const { service } = createService({ key: null });
  await assert.rejects(
    () =>
      service.describe('user-1', [
        {
          name: 'annotated.png',
          mimeType: 'image/png',
          dataBase64: PNG_BASE64,
        },
      ]),
    ServiceUnavailableException,
  );
});

test('managed vision rejects invalid base64 and mismatched image signatures', async () => {
  const { service } = createService();
  await assert.rejects(
    () =>
      service.describe('user-1', [
        {
          name: 'fake.png',
          mimeType: 'image/png',
          dataBase64: Buffer.from('not a png').toString('base64'),
        },
      ]),
    BadRequestException,
  );
  await assert.rejects(
    () =>
      service.describe('user-1', [
        {
          name: 'broken.png',
          mimeType: 'image/png',
          dataBase64: '%%%not-base64%%%',
        },
      ]),
    BadRequestException,
  );
});

test('managed vision keeps its key server-side and returns bounded annotation evidence', async () => {
  const originalFetch = globalThis.fetch;
  let authorization = '';
  let requestBody: Record<string, unknown> | undefined;
  globalThis.fetch = async (_input, init) => {
    authorization = String(
      (init?.headers as Record<string, string> | undefined)?.Authorization ??
        '',
    );
    requestBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return new Response(
      JSON.stringify({
        choices: [
          {
            message: {
              content:
                '```json\n{"descriptions":[{"index":0,"description":"A red hand-drawn circle surrounds the missing attachment control beside the composer."}]}\n```',
            },
          },
        ],
        usage: { prompt_tokens: 120, completion_tokens: 30 },
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    );
  };

  try {
    const { service, writes } = createService();
    const result = await service.describe('user-1', [
      {
        name: 'annotated.png',
        mimeType: 'image/png',
        dataBase64: PNG_BASE64,
      },
    ]);

    assert.equal(authorization, 'Bearer platform-vision-secret');
    assert.equal(requestBody?.model, 'glm-4.6v-flash');
    assert.deepEqual(requestBody?.thinking, { type: 'disabled' });
    assert.equal(requestBody?.max_tokens, 4096);
    assert.deepEqual(result, {
      descriptions: [
        {
          name: 'annotated.png',
          description:
            'A red hand-drawn circle surrounds the missing attachment control beside the composer.',
        },
      ],
      provider: 'glm',
      model: 'glm-4.6v-flash',
      route: 'founder-managed-vision',
    });
    assert.equal(writes.length, 1);
    assert.match(JSON.stringify(writes[0]), /ide\.annotated_screenshot/);
    assert.doesNotMatch(JSON.stringify(result), /platform-vision-secret/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('managed vision rejects incomplete or malformed provider descriptions', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        choices: [
          {
            message: {
              content: '{"descriptions":[]}',
            },
          },
        ],
      }),
      { status: 200 },
    );
  try {
    const { service } = createService();
    await assert.rejects(
      () =>
        service.describe('user-1', [
          {
            name: 'annotated.png',
            mimeType: 'image/png',
            dataBase64: PNG_BASE64,
          },
        ]),
      BadGatewayException,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('managed vision redacts provider error bodies and platform credentials', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response('upstream included platform-vision-secret', { status: 401 });
  try {
    const { service } = createService();
    await assert.rejects(
      () =>
        service.describe('user-1', [
          {
            name: 'annotated.png',
            mimeType: 'image/png',
            dataBase64: PNG_BASE64,
          },
        ]),
      (error: unknown) => {
        assert.ok(error instanceof BadGatewayException);
        assert.match(error.message, /401/);
        assert.doesNotMatch(error.message, /platform-vision-secret/);
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('managed vision bounds provider response data before JSON parsing', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response('x'.repeat(1_000_001), { status: 200 });
  try {
    const { service } = createService();
    await assert.rejects(
      () =>
        service.describe('user-1', [
          {
            name: 'annotated.png',
            mimeType: 'image/png',
            dataBase64: PNG_BASE64,
          },
        ]),
      BadGatewayException,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
