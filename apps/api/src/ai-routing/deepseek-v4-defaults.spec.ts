import assert from 'node:assert/strict';
import test from 'node:test';
import {
  DEEPSEEK_V4_FLASH_MODEL,
  DEEPSEEK_V4_PRO_MODEL,
  normalizeRetiredDeepseekModel,
} from '../ai-proxy/deepseek-model-policy';
import { PROVIDER_SEEDS } from './ai-routing.constants';
import { AiRoutingService } from './ai-routing.service';

test('shared DeepSeek defaults use supported v4 models and preserve custom models', () => {
  assert.equal(normalizeRetiredDeepseekModel(), DEEPSEEK_V4_FLASH_MODEL);
  assert.equal(
    normalizeRetiredDeepseekModel('deepseek-chat'),
    DEEPSEEK_V4_FLASH_MODEL,
  );
  assert.equal(
    normalizeRetiredDeepseekModel('deepseek-reasoner'),
    DEEPSEEK_V4_PRO_MODEL,
  );
  assert.equal(
    normalizeRetiredDeepseekModel('future-deepseek-model'),
    'future-deepseek-model',
  );
  assert.equal(
    PROVIDER_SEEDS.find((provider) => provider.key === 'deepseek')?.defaultModel,
    DEEPSEEK_V4_FLASH_MODEL,
  );
});

test('routing seed upgrades only retired DeepSeek aliases', async () => {
  const updateManyCalls: unknown[] = [];
  const prisma = {
    aiRoutingProvider: {
      upsert: async () => undefined,
      updateMany: async (input: unknown) => {
        updateManyCalls.push(input);
        return { count: 1 };
      },
    },
    aiSectionRouting: {
      upsert: async () => undefined,
    },
  };
  const service = new AiRoutingService(prisma as never, {} as never);

  await service.seedDefaults();

  assert.deepEqual(updateManyCalls, [
    {
      where: {
        key: 'deepseek',
        defaultModel: { in: ['deepseek-chat', 'deepseek-reasoner'] },
      },
      data: {
        defaultModel: DEEPSEEK_V4_FLASH_MODEL,
      },
    },
  ]);
});
