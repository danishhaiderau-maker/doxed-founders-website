import assert from 'node:assert/strict';
import test from 'node:test';
import {
  DEEPSEEK_V4_FLASH_MODEL,
  DEEPSEEK_V4_PRO_MODEL,
  forcedIntentForAlias,
  normalizeProviderModel,
} from './deepseek-model-policy';

test('Founder OS aliases force their advertised routing intent', () => {
  assert.equal(forcedIntentForAlias('founder-os-fast'), 'simple_qa');
  assert.equal(forcedIntentForAlias('founder-os-reasoning'), 'reasoning');
  assert.equal(forcedIntentForAlias('founder-os-code'), 'code');
  assert.equal(forcedIntentForAlias('founder-os-auto'), null);
});

test('retired DeepSeek route names are translated to supported v4 models', () => {
  assert.equal(
    normalizeProviderModel('deepseek', 'deepseek-chat', 'fast'),
    DEEPSEEK_V4_FLASH_MODEL,
  );
  assert.equal(
    normalizeProviderModel('deepseek', 'deepseek-coder-v2', 'code'),
    DEEPSEEK_V4_PRO_MODEL,
  );
  assert.equal(
    normalizeProviderModel('deepseek', 'deepseek-reasoner', 'reasoning'),
    DEEPSEEK_V4_PRO_MODEL,
  );
});

test('DeepSeek model always follows the effective tier, including cached v4 routes', () => {
  assert.equal(
    normalizeProviderModel('deepseek', DEEPSEEK_V4_FLASH_MODEL, 'fast'),
    DEEPSEEK_V4_FLASH_MODEL,
  );
  assert.equal(
    normalizeProviderModel('deepseek', DEEPSEEK_V4_PRO_MODEL, 'code'),
    DEEPSEEK_V4_PRO_MODEL,
  );
  assert.equal(
    normalizeProviderModel('deepseek', DEEPSEEK_V4_PRO_MODEL, 'fast'),
    DEEPSEEK_V4_FLASH_MODEL,
  );
  assert.equal(
    normalizeProviderModel('deepseek', DEEPSEEK_V4_FLASH_MODEL, 'reasoning'),
    DEEPSEEK_V4_PRO_MODEL,
  );
  assert.equal(normalizeProviderModel('glm', 'glm-5.2', 'reasoning'), 'glm-5.2');
});
