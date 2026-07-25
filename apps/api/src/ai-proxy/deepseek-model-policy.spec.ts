import assert from 'node:assert/strict';
import test from 'node:test';
import {
  DEEPSEEK_V4_FLASH_MODEL,
  DEEPSEEK_V4_PRO_MODEL,
  forcedIntentForAlias,
  normalizeFounderAliasRoute,
  normalizeProviderModel,
  normalizeProxyRoute,
} from './deepseek-model-policy';

test('Founder OS aliases force their advertised routing intent', () => {
  assert.equal(forcedIntentForAlias('founder-os-fast'), 'simple_qa');
  assert.equal(forcedIntentForAlias('founder-os-auto'), 'simple_qa');
  assert.equal(forcedIntentForAlias('founder-os-reasoning'), 'reasoning');
  assert.equal(forcedIntentForAlias('founder-os-code'), 'code');
  assert.equal(forcedIntentForAlias('deepseek-v4-flash'), null);
});

test('unsupported cached providers fail over to a supported DeepSeek model', () => {
  assert.deepEqual(normalizeProxyRoute('kimi', 'kimi-k2', 'fast'), {
    providerKey: 'deepseek',
    model: DEEPSEEK_V4_FLASH_MODEL,
    wasUnsupported: true,
  });
  assert.deepEqual(normalizeProxyRoute('local-playwright', 'chromium-headless', 'code'), {
    providerKey: 'deepseek',
    model: DEEPSEEK_V4_PRO_MODEL,
    wasUnsupported: true,
  });
});

test('Founder aliases enforce the proven DeepSeek adapter', () => {
  assert.deepEqual(
    normalizeFounderAliasRoute(
      'founder-os-auto',
      'deepseek',
      DEEPSEEK_V4_PRO_MODEL,
      'fast',
    ),
    {
      providerKey: 'deepseek',
      model: DEEPSEEK_V4_FLASH_MODEL,
      wasUnsupported: false,
    },
  );
  assert.deepEqual(
    normalizeFounderAliasRoute('founder-os-code', 'glm', 'glm-5.2', 'code'),
    {
      providerKey: 'deepseek',
      model: DEEPSEEK_V4_PRO_MODEL,
      wasUnsupported: true,
    },
  );
  assert.deepEqual(
    normalizeFounderAliasRoute('founder-os-fast', 'kimi', 'kimi-k2', 'fast'),
    {
      providerKey: 'deepseek',
      model: DEEPSEEK_V4_FLASH_MODEL,
      wasUnsupported: true,
    },
  );
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
