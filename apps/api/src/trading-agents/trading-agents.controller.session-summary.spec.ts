import 'reflect-metadata';
import assert from 'node:assert/strict';
import test from 'node:test';
import { PATH_METADATA } from '@nestjs/common/constants';
import { TradingAgentsController } from './trading-agents.controller';

test('session-summary is a neutral public alias for analyzer summary data', async () => {
  const calls: string[] = [];
  const tradingAgents = {
    getAnalyzerSummary: async (slug: string) => {
      calls.push(slug);
      return { ok: true, slug };
    },
  };
  const controller = new TradingAgentsController(
    tradingAgents as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );

  assert.equal(
    Reflect.getMetadata(PATH_METADATA, controller.sessionSummary),
    ':slug/session-summary',
  );
  assert.deepEqual(await controller.sessionSummary('conservative-btc'), {
    ok: true,
    slug: 'conservative-btc',
  });
  assert.deepEqual(calls, ['conservative-btc']);
});
