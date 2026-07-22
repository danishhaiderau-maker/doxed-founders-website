import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { afterEach, describe, it } from 'node:test';
import {
  containsSensitiveMaterial,
  FounderSafeResultCache,
  isSafeReadOnlyPrompt,
  semanticReadOnlyKey,
} from './safe-result-cache';

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-safe-result-'));
  roots.push(root);
  return {
    cache: new FounderSafeResultCache(root, 1_000),
    input: {
      prompt: 'Can you explain the session token flow?',
      model: 'founder-os-fast',
      context: {
        workspaceId: 'workspace-1',
        contextHash: 'a'.repeat(64),
        files: [{ path: 'src/session.ts', sha256: 'b'.repeat(64) }],
      },
    },
  };
}

describe('Founder safe result cache', () => {
  it('accepts read-only questions and rejects mutation requests', () => {
    assert.equal(isSafeReadOnlyPrompt('What does this session service do?'), true);
    assert.equal(isSafeReadOnlyPrompt('Please fix this session service'), false);
    assert.equal(isSafeReadOnlyPrompt('Run the tests and explain failures'), false);
  });

  it('normalizes harmless conversational wording without reordering intent', () => {
    assert.equal(
      semanticReadOnlyKey('Could you please explain the Session flow?'),
      'explain the session flow',
    );
  });

  it('reuses only the same model and relevant context hash', () => {
    const { cache, input } = fixture();
    assert.equal(cache.put(input, 'The session rotates after authentication.', 450, 100), true);
    assert.equal(cache.get(input, 200)?.estimatedTokensAvoided, 450);
    assert.equal(cache.get({ ...input, model: 'founder-os-reasoning' }, 200), null);
    assert.equal(cache.get({ ...input, context: { ...input.context, contextHash: 'c'.repeat(64) } }, 200), null);
  });

  it('expires entries and refuses to persist likely secrets', () => {
    const { cache, input } = fixture();
    assert.equal(cache.put(input, 'api_key=sk-test-secret-value-123456789', 100, 100), false);
    assert.equal(containsSensitiveMaterial('password: super-secret-value'), true);
    assert.equal(cache.put(input, 'Safe explanation.', 100, 100), true);
    assert.equal(cache.get(input, 1_101), null);
  });

  it('invalidates every semantic result for one changed workspace', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-result-cache-'));
    const cache = new FounderSafeResultCache(root, 10_000);
    const first = {
      prompt: 'explain the session flow',
      model: 'founder-os-auto',
      context: { workspaceId: 'first', contextHash: 'a'.repeat(64), files: [] },
    };
    const second = {
      ...first,
      context: { workspaceId: 'second', contextHash: 'b'.repeat(64), files: [] },
    };
    assert.equal(cache.put(first, 'First result.', 100), true);
    assert.equal(cache.put(second, 'Second result.', 100), true);
    assert.equal(cache.invalidateWorkspace('first'), 1);
    assert.equal(cache.get(first), null);
    assert.equal(cache.get(second)?.text, 'Second result.');
  });
});
