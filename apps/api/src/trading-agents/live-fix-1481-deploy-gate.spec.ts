import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// eslint-disable-next-line @typescript-eslint/no-var-requires
const gate = require('../../scripts/relay-executor-predeploy-gate.cjs');

function fakeClock() {
  let t = 0;
  return { now: () => t, sleep: async (ms: number) => { t += ms; } };
}

test('1481: gate allows the swap only after consecutive flat reads', async () => {
  const seq = [1, 1, 0, 0];
  const clock = fakeClock();
  const logs: string[] = [];
  const prisma = { $queryRawUnsafe: async () => [{ n: seq.length ? seq.shift() : 0 }] };
  const code = await gate.runGate({ prisma, ...clock, log: (m: string) => logs.push(m), maxWaitMs: 60_000 });
  assert.equal(code, 0);
  assert.ok(logs.some((l) => l.includes('swap allowed')));
});

test('1481: a flat read interrupted by a new entry restarts the streak', async () => {
  const seq = [0, 1, 0, 0];
  let reads = 0;
  const clock = fakeClock();
  const prisma = { $queryRawUnsafe: async () => { reads += 1; return [{ n: seq.length ? seq.shift() : 0 }]; } };
  assert.equal(await gate.runGate({ prisma, ...clock, log: () => {}, maxWaitMs: 60_000 }), 0);
  assert.equal(reads, 4);
});

test('1481: persistent exposure or unreadable DB refuses the deploy (fail closed)', async () => {
  const clock = fakeClock();
  assert.equal(await gate.runGate({ prisma: { $queryRawUnsafe: async () => [{ n: 1 }] }, ...clock, log: () => {}, maxWaitMs: 10_000 }), 1);
  const clock2 = fakeClock();
  assert.equal(await gate.runGate({ prisma: { $queryRawUnsafe: async () => { throw new Error('down'); } }, ...clock2, log: () => {}, maxWaitMs: 10_000 }), 1);
});

test('1481: exposure query covers OPEN and PENDING_ENTRY Bitfinex participants', () => {
  assert.match(gate.EXPOSURE_SQL, /'OPEN', 'PENDING_ENTRY'/);
  assert.match(gate.EXPOSURE_SQL, /"exchangeProvider" = 'bitfinex'/);
});

test('1481: Railway executor config runs the gate before every swap', () => {
  const toml = readFileSync(join(__dirname, '../../../../railway.relay-executor.toml'), 'utf8');
  assert.match(toml, /preDeployCommand = \["node apps\/api\/scripts\/relay-executor-predeploy-gate\.cjs"\]/);
});
