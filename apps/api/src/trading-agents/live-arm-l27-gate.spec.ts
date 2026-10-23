import 'reflect-metadata';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { ConflictException } from '@nestjs/common';
import {
  DENY_L27_GATE_UNREADABLE,
  DENY_L27_LATEST_L,
  FLY_ARM_GATE_PATH,
  FLY_ARM_GATE_SCHEMA,
  fetchL27ArmBlock,
  l27ArmGateVerdict,
} from './live-arm-l27-gate';
import { TradingAgentInstancesService } from './trading-agent-instances.service';
import { TradingAgentsController } from './trading-agents.controller';

const ok = { schema: FLY_ARM_GATE_SCHEMA, l27_block: null };
const blocked = { schema: FLY_ARM_GATE_SCHEMA, l27_block: DENY_L27_LATEST_L };

test('L27 gate: same code as the shared Python helper; verdict is pure and fails closed', () => {
  const py = readFileSync(join(__dirname, '../../../../services/btc-conservative-agent/live_tile_gate.py'), 'utf8');
  assert.match(py, new RegExp(`DENY_L27_LATEST_L = "${DENY_L27_LATEST_L}"`));
  const bot = readFileSync(join(__dirname, '../../../../services/btc-conservative-agent/bot.py'), 'utf8');
  assert.match(bot, new RegExp(`LIVE_ARM_GATE_SCHEMA = "${FLY_ARM_GATE_SCHEMA}"`));
  assert.match(bot, new RegExp(`@app.route\\('${FLY_ARM_GATE_PATH}'`));
  assert.equal(l27ArmGateVerdict(ok), null);
  assert.equal(l27ArmGateVerdict(blocked), DENY_L27_LATEST_L);
  for (const bad of [null, undefined, 'x', {}, { schema: 'other', l27_block: null }, { schema: FLY_ARM_GATE_SCHEMA },
    { schema: FLY_ARM_GATE_SCHEMA, l27_block: '' }, { schema: FLY_ARM_GATE_SCHEMA, l27_block: 5 }]) {
    assert.equal(l27ArmGateVerdict(bad), DENY_L27_GATE_UNREADABLE);
  }
});

test('L27 gate: fetch reads Fly /api/live-copy/arm-gate with the admin header; any failure => UNREADABLE', async () => {
  const seen: Array<{ url: string; token?: string }> = [];
  const mk = (r: () => Promise<Response>) => (async (url: string, init?: RequestInit) => {
    seen.push({ url, token: (init?.headers as Record<string, string>)['X-Bot-Admin-Token'] });
    return r();
  }) as unknown as typeof fetch;
  const env = { FLY_BOT_URL: 'https://fly.example/', BOT_ADMIN_TOKEN: 't0k' } as NodeJS.ProcessEnv;
  assert.equal(await fetchL27ArmBlock(mk(async () => new Response(JSON.stringify(ok))), env), null);
  assert.deepEqual(seen[0], { url: `https://fly.example${FLY_ARM_GATE_PATH}`, token: 't0k' });
  assert.equal(await fetchL27ArmBlock(mk(async () => new Response(JSON.stringify(blocked))), env), DENY_L27_LATEST_L);
  assert.equal(await fetchL27ArmBlock(mk(async () => new Response('{}', { status: 401 })), env), DENY_L27_GATE_UNREADABLE);
  assert.equal(await fetchL27ArmBlock(mk(async () => new Response('not json')), env), DENY_L27_GATE_UNREADABLE);
  assert.equal(await fetchL27ArmBlock(mk(async () => { throw new Error('ECONNREFUSED'); }), env), DENY_L27_GATE_UNREADABLE);
});

function instancesSvc(block: string | null, instance: Record<string, unknown> | null) {
  const s = Object.create(TradingAgentInstancesService.prototype) as TradingAgentInstancesService;
  const calls: string[] = [];
  Object.assign(s as unknown as Record<string, unknown>, {
    logger: { warn: () => undefined, log: () => undefined },
    liveArmL27Block: async () => { calls.push('l27'); return block; },
    prisma: {
      tradingAgent: { findUnique: async () => ({ id: 'a1', slug: 'conservative-btc', costDdollarWeek: 0, costDdollarDay: 0 }) },
      tradingAgentInstance: {
        findUnique: async () => instance,
        update: async () => { calls.push('update'); return instance; },
        upsert: async () => { calls.push('upsert'); return instance; },
      },
    },
    exchanges: { connectUserExchange: async () => { calls.push('connect'); return { credentialId: 'c' }; } },
  });
  return { s, calls };
}

test('L27 path instance/resume: setInstancePaused(false) on a live instance refuses before any status write', async () => {
  const live = { id: 'i1', exchangeProvider: 'bitfinex', dashboardState: {}, expiresAt: null, status: 'PAUSED' };
  const { s, calls } = instancesSvc(DENY_L27_LATEST_L, live);
  await assert.rejects(s.setInstancePaused('u', 'conservative-btc', false), (e: unknown) =>
    e instanceof ConflictException && JSON.stringify(e.getResponse()).includes(DENY_L27_LATEST_L)
    && JSON.stringify(e.getResponse()).includes('instance/resume'));
  assert.deepEqual(calls, ['l27']);
  const unread = instancesSvc(DENY_L27_GATE_UNREADABLE, live);
  await assert.rejects(unread.s.setInstancePaused('u', 'conservative-btc', false), ConflictException);
  assert.deepEqual(unread.calls, ['l27']);
});

test('L27 path instance/resume: pausing and paper resumes never consult or wait on the arm gate', async () => {
  const live = { id: 'i1', exchangeProvider: 'bitfinex', dashboardState: {}, expiresAt: null };
  const { s, calls } = instancesSvc(DENY_L27_LATEST_L, live);
  await s.setInstancePaused('u', 'conservative-btc', true).catch(() => undefined);
  assert.equal(calls.includes('l27'), false);
  const paper = instancesSvc(DENY_L27_LATEST_L, { ...live, exchangeProvider: 'paper' });
  await paper.s.setInstancePaused('u', 'conservative-btc', false).catch(() => undefined);
  assert.equal(paper.calls.includes('l27'), false);
});

test('L27 path hire: a live hire (creates an ACTIVE instance) refuses before connecting keys or writing', async () => {
  const { s, calls } = instancesSvc(DENY_L27_LATEST_L, null);
  await assert.rejects(
    s.hireAgent('u', 'a1', { exchangeProvider: 'bitfinex', apiKey: 'k', apiSecret: 's' }),
    (e: unknown) => e instanceof ConflictException && JSON.stringify(e.getResponse()).includes('"path":"hire"'),
  );
  assert.deepEqual(calls, ['l27']);
});

test('L27 path ops/rearm: refused BEFORE the flatten and the arm; allowed verdict proceeds', async () => {
  const prev = process.env.BOT_ADMIN_TOKEN;
  process.env.BOT_ADMIN_TOKEN = 'valid-token';
  try {
    const calls: string[] = [];
    const mk = (block: string | null) => {
      const instances = {
        assertLiveArmL27: async (path: string, provider: string) => {
          calls.push(`l27:${path}:${provider}`);
          if (block) throw new ConflictException({ ok: false, error: block, path });
        },
        setInstancePaused: async (_u: string, _s: string, paused: boolean) => { calls.push(`pause:${paused}`); return { ok: true }; },
      };
      const execution = { emergencyFlattenOpenCopyLots: async () => { calls.push('flatten'); return { flattened: 0 }; } };
      return new TradingAgentsController({} as never, instances as never, {} as never, {} as never, execution as never);
    };
    const body = { confirmation: 'FORCE_FLAT_THEN_REARM' };
    await assert.rejects(mk(DENY_L27_LATEST_L).opsRearm('conservative-btc', 'u', 'valid-token', undefined, body), ConflictException);
    assert.deepEqual(calls, ['l27:ops/rearm:bitfinex']);
    calls.length = 0;
    await mk(null).opsRearm('conservative-btc', 'u', 'valid-token', undefined, body);
    assert.deepEqual(calls, ['l27:ops/rearm:bitfinex', 'flatten', 'pause:false']);
  } finally {
    if (prev === undefined) delete process.env.BOT_ADMIN_TOKEN; else process.env.BOT_ADMIN_TOKEN = prev;
  }
});

test('L27: every relay status->ACTIVE write for a live instance sits behind assertLiveArmL27', () => {
  const src = readFileSync(join(__dirname, 'trading-agent-instances.service.ts'), 'utf8');
  const hire = src.slice(src.indexOf('async hireAgent('), src.indexOf('async paperTrackAgent('));
  assert.ok(hire.indexOf("assertLiveArmL27('hire'") > 0);
  assert.ok(hire.indexOf("assertLiveArmL27('hire'") < hire.indexOf('status: TradingAgentInstanceStatus.ACTIVE'));
  const resume = src.slice(src.indexOf('async setInstancePaused('));
  assert.ok(resume.indexOf("assertLiveArmL27('instance/resume'") > 0);
  assert.ok(resume.indexOf("assertLiveArmL27('instance/resume'") < resume.indexOf('TradingAgentInstanceStatus.ACTIVE'));
  const ctl = readFileSync(join(__dirname, 'trading-agents.controller.ts'), 'utf8');
  const rearm = ctl.slice(ctl.indexOf("@Post(':slug/ops/rearm')"), ctl.indexOf("@Post(':slug/ops/force-flat-disarm')"));
  assert.ok(rearm.indexOf("assertLiveArmL27('ops/rearm'") < rearm.indexOf('emergencyFlattenOpenCopyLots('));
});
