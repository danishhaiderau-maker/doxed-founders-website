import assert from 'node:assert/strict';
import test from 'node:test';
import {
  describePersonalVisuals,
  parsePersonalVisualEvidence,
} from './personal-visual-request';
import type { PersonalAiProfileSecret } from './personal-ai-profiles';

const screenshot = {
  name: 'annotated.png',
  mimeType: 'image/png',
  dataBase64: Buffer.alloc(48).toString('base64'),
};

function profile(
  overrides: Partial<PersonalAiProfileSecret> = {},
): PersonalAiProfileSecret {
  return {
    id: 'visual-profile',
    name: 'Founder private vision',
    kind: 'openai-compatible',
    baseUrl: 'https://provider.example/v1',
    apiKey: 'private-key',
    model: 'coding-model',
    visionModel: 'vision-model',
    useForVisuals: true,
    headers: { 'X-Project': 'founder' },
    enabled: true,
    hasApiKey: true,
    headerNames: ['X-Project'],
    createdAt: '2026-07-26T00:00:00.000Z',
    updatedAt: '2026-07-26T00:00:00.000Z',
    ...overrides,
  };
}

const evidence = JSON.stringify({
  images: [
    {
      name: 'annotated.png',
      layout: 'Founder IDE with a navigation rail on the left.',
      visibleText: ['Founder', 'New chat'],
      annotations: [
        {
          type: 'circle',
          description: 'A red circle marks unused navigation space.',
          target: 'the left activity rail',
          confidence: 0.97,
        },
        {
          type: 'arrow',
          description: 'A red arrow points toward the settings button.',
          target: 'the lower-left settings icon',
          confidence: 0.91,
        },
      ],
      likelyIntent: 'Make the founder navigation clearer and use the empty rail.',
      uncertainties: [],
    },
  ],
});

test('personal vision uses the selected model and returns annotation-aware evidence', async () => {
  let requestUrl = '';
  let authorization = '';
  let requestModel = '';
  const result = await describePersonalVisuals(
    { attachments: [screenshot] },
    profile(),
    {
      platform: 'linux',
      fetchImpl: (async (url, init) => {
        requestUrl = String(url);
        authorization = String(
          (init?.headers as Record<string, string>).Authorization,
        );
        requestModel = JSON.parse(String(init?.body)).model as string;
        return new Response(
          JSON.stringify({
            choices: [{ message: { content: evidence } }],
          }),
          { status: 200 },
        );
      }) as typeof fetch,
    },
  );

  assert.equal(requestUrl, 'https://provider.example/v1/chat/completions');
  assert.equal(authorization, 'Bearer private-key');
  assert.equal(requestModel, 'vision-model');
  assert.equal(result.route, 'founder-personal-vision');
  assert.equal(result.outsideManagedQuota, true);
  assert.match(result.descriptions[0].description, /Founder annotations/);
  assert.match(result.descriptions[0].description, /circle/);
  assert.match(result.descriptions[0].description, /left activity rail/);
});

test('local Ollama vision uses native multimodal chat with raw image bytes', async () => {
  let requestUrl = '';
  let body: {
    model?: string;
    messages?: Array<{ images?: string[] }>;
  } = {};
  const result = await describePersonalVisuals(
    { attachments: [screenshot] },
    profile({
      kind: 'ollama',
      baseUrl: 'http://127.0.0.1:11434/v1',
      apiKey: '',
      headers: {},
      hasApiKey: false,
      headerNames: [],
      model: 'qwen3-coder',
      visionModel: 'qwen2.5vl',
    }),
    {
      platform: 'linux',
      fetchImpl: (async (url, init) => {
        requestUrl = String(url);
        body = JSON.parse(String(init?.body)) as typeof body;
        return new Response(
          JSON.stringify({ message: { content: evidence } }),
          { status: 200 },
        );
      }) as typeof fetch,
    },
  );

  assert.equal(requestUrl, 'http://127.0.0.1:11434/api/chat');
  assert.equal(body.model, 'qwen2.5vl');
  assert.deepEqual(body.messages?.[1]?.images, [screenshot.dataBase64]);
  assert.equal(result.route, 'founder-local-vision');
});

test('visual evidence rejects incomplete or unstructured model output', () => {
  assert.throws(
    () => parsePersonalVisualEvidence('not json', ['annotated.png']),
    /structured visual evidence/,
  );
  assert.throws(
    () => parsePersonalVisualEvidence('{"images":[]}', ['annotated.png']),
    /describe every attachment/,
  );
});

test('personal vision fails closed without exposing response bodies or keys', async () => {
  const privateProfile = profile();
  await assert.rejects(
    describePersonalVisuals(
      { attachments: [screenshot] },
      privateProfile,
      {
        platform: 'linux',
        fetchImpl: (async () => new Response(
          'upstream diagnostic includes private-key',
          { status: 401 },
        )) as typeof fetch,
      },
    ),
    (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.match(error.message, /HTTP 401/);
      assert.doesNotMatch(error.message, /private-key|upstream diagnostic/);
      return true;
    },
  );
});
