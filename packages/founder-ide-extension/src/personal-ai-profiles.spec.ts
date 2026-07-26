import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type * as vscode from 'vscode';
import {
  normalizePersonalAiBaseUrl,
  parsePersonalAiHeaders,
  personalAiApiBase,
  PersonalAiProfileStore,
  probePersonalAiProfile,
  validatePersonalAiProfile,
  type PersonalAiProfileSecret,
} from './personal-ai-profiles';

function fakeContext() {
  const secrets = new Map<string, string>();
  const state = new Map<string, unknown>();
  return {
    context: {
      secrets: {
        get: async (key: string) => secrets.get(key),
        store: async (key: string, value: string) => { secrets.set(key, value); },
        delete: async (key: string) => { secrets.delete(key); },
      },
      globalState: {
        get: <T>(key: string) => state.get(key) as T | undefined,
        update: async (key: string, value: unknown) => {
          if (value === undefined) state.delete(key);
          else state.set(key, value);
        },
      },
    } as unknown as vscode.ExtensionContext,
    secrets,
  };
}

function secret(overrides: Partial<PersonalAiProfileSecret> = {}): PersonalAiProfileSecret {
  return {
    id: 'profile-1',
    name: 'Local model',
    kind: 'ollama',
    baseUrl: 'http://127.0.0.1:11434',
    apiKey: '',
    model: 'qwen3-coder',
    visionModel: 'qwen3-coder',
    useForVisuals: false,
    headers: {},
    enabled: true,
    hasApiKey: false,
    headerNames: [],
    createdAt: '2026-07-22T00:00:00.000Z',
    updatedAt: '2026-07-22T00:00:00.000Z',
    ...overrides,
  };
}

describe('personal AI profile policy', () => {
  it('requires HTTPS remotely but permits local and private endpoints', () => {
    assert.throws(
      () => normalizePersonalAiBaseUrl('http://provider.example/v1'),
      /must use HTTPS/,
    );
    assert.equal(
      normalizePersonalAiBaseUrl('http://127.0.0.1:11434/'),
      'http://127.0.0.1:11434',
    );
    assert.equal(
      normalizePersonalAiBaseUrl('http://192.168.1.9:8080/v1/chat/completions'),
      'http://192.168.1.9:8080/v1',
    );
  });

  it('rejects unsafe header overrides and preserves encrypted edit values', () => {
    assert.throws(() => parsePersonalAiHeaders('{"Authorization":"leak"}'), /managed by Founder/);
    const existing = secret({
      kind: 'openai-compatible',
      baseUrl: 'https://provider.example/v1',
      apiKey: 'secret-key',
      headers: { 'X-Organization': 'founder' },
    });
    const edited = validatePersonalAiProfile({
      id: existing.id,
      name: 'Updated',
      kind: 'openai-compatible',
      baseUrl: existing.baseUrl,
      model: 'model-v2',
    }, existing);
    assert.equal(edited.apiKey, 'secret-key');
    assert.deepEqual(edited.headers, { 'X-Organization': 'founder' });
  });

  it('requires a key for a new remote OpenAI-compatible profile', () => {
    assert.throws(() => validatePersonalAiProfile({
      name: 'Remote',
      kind: 'openai-compatible',
      baseUrl: 'https://provider.example/v1',
      model: 'model-v1',
    }), /API key is required/);
  });

  it('adds the Ollama OpenAI compatibility path', () => {
    assert.equal(personalAiApiBase(secret()), 'http://127.0.0.1:11434/v1');
  });

  it('supports a separate screenshot model without changing the chat model', () => {
    const profile = validatePersonalAiProfile({
      name: 'Local multimodal',
      kind: 'ollama',
      baseUrl: 'http://127.0.0.1:11434',
      model: 'qwen3-coder',
      visionModel: 'qwen2.5vl',
      useForVisuals: true,
    });
    assert.equal(profile.model, 'qwen3-coder');
    assert.equal(profile.visionModel, 'qwen2.5vl');
    assert.equal(profile.useForVisuals, true);
  });
});

describe('PersonalAiProfileStore', () => {
  it('stores secrets outside public summaries and clears disabled selection', async () => {
    const { context, secrets } = fakeContext();
    const store = new PersonalAiProfileStore(context);
    await store.ready();
    const saved = await store.save({
      name: 'DeepSeek personal',
      kind: 'openai-compatible',
      baseUrl: 'https://api.deepseek.com',
      apiKey: 'top-secret',
      model: 'deepseek-v4-pro',
      headers: { 'X-Project': 'founder' },
    });
    assert.equal(saved.hasApiKey, true);
    assert.equal('apiKey' in saved, false);
    assert.match([...secrets.values()][0] ?? '', /top-secret/);
    await store.select(saved.id);
    assert.equal(store.activeId(), saved.id);
    await store.setEnabled(saved.id, false);
    assert.equal(store.activeId(), null);
    store.dispose();
  });

  it('keeps exactly one enabled screenshot-reading profile', async () => {
    const { context } = fakeContext();
    const store = new PersonalAiProfileStore(context);
    await store.ready();
    const first = await store.save({
      name: 'Local vision',
      kind: 'ollama',
      baseUrl: 'http://127.0.0.1:11434',
      model: 'qwen3-coder',
      visionModel: 'qwen2.5vl',
      useForVisuals: true,
    });
    const second = await store.save({
      name: 'Personal vision',
      kind: 'openai-compatible',
      baseUrl: 'https://provider.example/v1',
      apiKey: 'private-key',
      model: 'coding-model',
      visionModel: 'vision-model',
      useForVisuals: true,
    });

    assert.equal(store.visual()?.id, second.id);
    assert.equal(
      store.list().find((profile) => profile.id === first.id)?.useForVisuals,
      false,
    );
    assert.equal(
      store.list().find((profile) => profile.id === second.id)?.useForVisuals,
      true,
    );
    store.dispose();
  });
});

describe('personal AI connectivity probe', () => {
  it('returns status and latency without exposing a key', async () => {
    const result = await probePersonalAiProfile(
      secret(),
      async () => new Response('{"models":[]}', { status: 200 }),
    );
    assert.equal(result.ok, true);
    assert.match(result.message, /Local model is reachable/);
  });
});
