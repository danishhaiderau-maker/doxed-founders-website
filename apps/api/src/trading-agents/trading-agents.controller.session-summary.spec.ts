import 'reflect-metadata';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { PATH_METADATA } from '@nestjs/common/constants';
import { TradingAgentsController } from './trading-agents.controller';

const read = (name: string) => readFileSync(join(__dirname, name), 'utf8');

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

test('showcase-host defaults to Fly and requires an explicit local override', () => {
  const previous = process.env.SHOWCASE_HOST;
  const controller = new TradingAgentsController(
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );

  try {
    delete process.env.SHOWCASE_HOST;
    assert.deepEqual(controller.showcaseHost(), { host: 'fly' });

    process.env.SHOWCASE_HOST = ' ';
    assert.deepEqual(controller.showcaseHost(), { host: 'fly' });

    process.env.SHOWCASE_HOST = ' local ';
    assert.deepEqual(controller.showcaseHost(), { host: 'local' });

    process.env.SHOWCASE_HOST = 'FLY';
    assert.deepEqual(controller.showcaseHost(), { host: 'fly' });
  } finally {
    if (previous === undefined) {
      delete process.env.SHOWCASE_HOST;
    } else {
      process.env.SHOWCASE_HOST = previous;
    }
  }
});

test('public API cannot start or stop the sole Fly risk-manager machine', () => {
  const controller = read('./trading-agents.controller.ts');
  const module = read('./trading-agents.module.ts');

  assert.doesNotMatch(controller, /fly-control|FlyControlService|flyControlStatus/);
  assert.doesNotMatch(module, /FlyControlService|fly-control\.service/);
});
