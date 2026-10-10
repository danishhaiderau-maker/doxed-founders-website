import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assertBotReadToken } from './ops-read-auth';

test('read-only ops auth accepts admin or monitor token, nothing else', () => {
  const prev = { a: process.env.BOT_ADMIN_TOKEN, m: process.env.MONITOR_READ_TOKEN };
  process.env.BOT_ADMIN_TOKEN = 'admin-token-x';
  process.env.MONITOR_READ_TOKEN = 'monitor-token-y';
  try {
    assert.doesNotThrow(() => assertBotReadToken('admin-token-x'));
    assert.doesNotThrow(() => assertBotReadToken(undefined, 'Bearer monitor-token-y'));
    assert.throws(() => assertBotReadToken('wrong'));
    assert.throws(() => assertBotReadToken(undefined, undefined));
    delete process.env.MONITOR_READ_TOKEN;
    assert.throws(() => assertBotReadToken('monitor-token-y'));
  } finally {
    if (prev.a === undefined) delete process.env.BOT_ADMIN_TOKEN; else process.env.BOT_ADMIN_TOKEN = prev.a;
    if (prev.m === undefined) delete process.env.MONITOR_READ_TOKEN; else process.env.MONITOR_READ_TOKEN = prev.m;
  }
});
