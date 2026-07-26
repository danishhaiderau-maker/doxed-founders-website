import assert from 'node:assert/strict';
import test from 'node:test';
import { AI_PROVIDERS, AI_PROVIDER_GUIDES } from '../ai-providers';

test('DeepSeek BYOK defaults and guidance use current v4 model IDs', () => {
  assert.equal(
    AI_PROVIDERS.find((provider) => provider.key === 'DEEPSEEK')?.defaultModel,
    'deepseek-v4-flash',
  );
  assert.equal(
    AI_PROVIDER_GUIDES.DEEPSEEK?.modelPlaceholder,
    'deepseek-v4-flash, deepseek-v4-pro',
  );
});
