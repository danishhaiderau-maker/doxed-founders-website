/**
 * #852 E6 + RK binding ruling (3): ONE shared Bitfinex read budget (per relay IP, per endpoint) for the exchange mirror,
 * the money tick, force-flat / position-flatten and #854's positions read. Pins: the total sent never reaches 90/min in any
 * 60 s window, even with the mirror at 1.25 s, a saturating money tick and #854 polling; flatten read-backs and every
 * order / stop / flatten WRITE are never starved; a timed-out evidence read is stamped E6_READ_TIMEOUT.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  BITFINEX_AUTH_CRITICAL_CAP_PER_MIN,
  BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN,
  BITFINEX_AUTH_ROUTINE_CAP_PER_MIN,
  BITFINEX_AUTH_TICK_CAP_PER_MIN,
  type BitfinexAuthReadClass,
  bitfinexAuthAdmission,
  bitfinexAuthPost,
  currentBitfinexReadClass,
  recordBitfinexAuthCall,
  resetBitfinexAuthCallLog,
  withBitfinexCriticalReads,
  withBitfinexReadClass,
} from '../exchanges/bitfinex-api.client';
import {
  EXCHANGE_MIRROR_AUTH_GUARD_PER_MIN,
  EXCHANGE_MIRROR_EXPOSED_EVERY_MS,
  EXCHANGE_MIRROR_READ_TIMEOUT_MS,
  exchangeMirrorErrorCode,
} from './signal-subscriber-execution.service';

const POS = 'v2/auth/r/positions';
const ORD = 'v2/auth/r/orders';

type Source = { name: string; cls: BitfinexAuthReadClass | 'write'; path: string; everyMs: number; fromMs?: number };

/** Discrete 50 ms simulation of every demand source against the real admission function. */
function simulate(sources: Source[], durationMs: number) {
  resetBitfinexAuthCallLog();
  const T0 = 10_000_000;
  const sent: Record<string, number[]> = {};
  const admitted: Record<string, number> = {};
  const refused: Record<string, number> = {};
  const next = new Map(sources.map((s) => [s.name, T0 + (s.fromMs ?? 0)]));
  for (let t = T0; t <= T0 + durationMs; t += 50) {
    for (const s of sources) {          // routine first, critical last: the worst order for the critical class
      while ((next.get(s.name) as number) <= t) {
        next.set(s.name, (next.get(s.name) as number) + s.everyMs);
        const path = s.cls === 'write' ? 'v2/auth/w/order/submit' : s.path;
        const a = bitfinexAuthAdmission(path, s.cls === 'write' ? 'tick' : s.cls, t);
        if (a.ok) {
          recordBitfinexAuthCall(path, t);
          (sent[path] ??= []).push(t);
          admitted[s.name] = (admitted[s.name] ?? 0) + 1;
        } else {
          assert.match(a.reason, /^BITFINEX_AUTH_BUDGET/);
          refused[s.name] = (refused[s.name] ?? 0) + 1;
        }
      }
    }
  }
  const maxWindow = (path: string) => {
    const xs = sent[path] ?? [];
    let best = 0;
    for (let i = 0, j = 0; i < xs.length; i++) {
      while (xs[i] - xs[j] >= 60_000) j++;
      best = Math.max(best, i - j + 1);
    }
    return best;
  };
  resetBitfinexAuthCallLog();
  return { admitted, refused, maxWindow };
}

test('RK (3): class caps share one budget with reserved headroom, all strictly below Bitfinex 90/min', () => {
  assert.equal(BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN, 90);
  assert.equal(BITFINEX_AUTH_CRITICAL_CAP_PER_MIN, 89);
  assert.equal(BITFINEX_AUTH_TICK_CAP_PER_MIN, 72);
  assert.equal(BITFINEX_AUTH_ROUTINE_CAP_PER_MIN, 54);
  assert.equal(EXCHANGE_MIRROR_AUTH_GUARD_PER_MIN, BITFINEX_AUTH_ROUTINE_CAP_PER_MIN, 'the mirror guard IS the routine cap');
  assert.ok(BITFINEX_AUTH_TICK_CAP_PER_MIN - BITFINEX_AUTH_ROUTINE_CAP_PER_MIN >= 18, 'money tick headroom over the mirror');
  assert.ok(BITFINEX_AUTH_CRITICAL_CAP_PER_MIN - BITFINEX_AUTH_TICK_CAP_PER_MIN >= 17, 'flatten headroom over the tick');
  assert.equal(EXCHANGE_MIRROR_EXPOSED_EVERY_MS, 1_750, 'cadence unchanged (1.25 s is modelled below, not shipped)');
});

for (const mirrorEveryMs of [1_250, EXCHANGE_MIRROR_EXPOSED_EVERY_MS]) {
  test(`RK (3) worst case: mirror ${mirrorEveryMs} ms x2 accounts + saturating tick + #854 + force-flat stays < 90/min; flatten + writes never starved`, () => {
    const sources: Source[] = [];
    for (const path of [ORD, POS]) {
      sources.push({ name: `mirrorA:${path}`, cls: 'routine', path, everyMs: mirrorEveryMs });
      sources.push({ name: `mirrorB:${path}`, cls: 'routine', path, everyMs: mirrorEveryMs, fromMs: 300 });
      sources.push({ name: `tick:${path}`, cls: 'tick', path, everyMs: 500 });                 // money tick: unpinned, saturating
      sources.push({ name: `flat:${path}`, cls: 'critical', path, everyMs: 5_000, fromMs: 60_000 }); // 6 force-flat + 6 position-flatten read-backs/min
    }
    sources.push({ name: 'pr854:positions', cls: 'routine', path: POS, everyMs: 1_000 });     // #854's positions read
    sources.push({ name: 'writes', cls: 'write', path: 'w', everyMs: 300 });                  // 200 order/stop/flatten writes/min
    const r = simulate(sources, 240_000);
    for (const path of [ORD, POS]) {
      const peak = r.maxWindow(path);
      assert.ok(peak < BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN, `${path} peak ${peak}/60s must be < 90`);
      assert.ok(peak <= BITFINEX_AUTH_CRITICAL_CAP_PER_MIN);
      assert.equal(r.refused[`flat:${path}`] ?? 0, 0, `flatten read-backs on ${path} are never refused`);
      assert.ok((r.admitted[`tick:${path}`] ?? 0) >= 18 * 4, `money tick on ${path} keeps >= 18/min`);
    }
    assert.equal(r.refused.writes ?? 0, 0, 'writes are never budget-refused');
    assert.ok((r.refused['tick:v2/auth/r/positions'] ?? 0) > 0, 'the saturating tick itself is what gets capped');
  });
}

test('RK (3): the shared budget refuses a routine read before anything is sent; critical scope survives awaits', async () => {
  resetBitfinexAuthCallLog();
  const now = Date.now();
  for (let i = 0; i < BITFINEX_AUTH_ROUTINE_CAP_PER_MIN; i++) recordBitfinexAuthCall(POS, now);
  const realFetch = globalThis.fetch;
  let fetched = 0;
  globalThis.fetch = (async () => { fetched += 1; throw new Error('must not be called'); }) as typeof fetch;
  try {
    const creds = { apiKey: 'k-test', apiSecret: 's-test' } as never;
    await assert.rejects(withBitfinexReadClass('routine', () => bitfinexAuthPost(creds, POS, {}, 2_000)), /^Error: BITFINEX_AUTH_BUDGET/);
    assert.equal(fetched, 0);
    assert.equal(bitfinexAuthAdmission(POS, 'tick').ok, true, 'the money tick still has headroom');
    assert.equal(bitfinexAuthAdmission(POS, 'critical').ok, true);
    assert.equal(bitfinexAuthAdmission('v2/auth/w/order/submit', 'routine').ok, true);
    assert.equal(currentBitfinexReadClass(), 'tick', 'unmarked reads are the tick class');
    const cls = await withBitfinexCriticalReads(async () => {
      await new Promise((r) => setTimeout(r, 5));
      return withBitfinexReadClass('critical', async () => { await Promise.resolve(); return currentBitfinexReadClass(); });
    });
    assert.equal(cls, 'critical');
  } finally {
    globalThis.fetch = realFetch;
    resetBitfinexAuthCallLog();
  }
});

test('RK (3): a timed-out evidence read is stamped E6_READ_TIMEOUT (2 s mirror read timeout)', () => {
  assert.equal(EXCHANGE_MIRROR_READ_TIMEOUT_MS, 2_000);
  assert.equal(exchangeMirrorErrorCode('Bitfinex v2/auth/r/positions: authenticated request total deadline exceeded after 2000ms'), 'E6_READ_TIMEOUT');
  assert.equal(exchangeMirrorErrorCode('Bitfinex v2/auth/r/orders: authenticated request queue deadline exceeded after 2000ms'), 'E6_READ_TIMEOUT');
  assert.equal(exchangeMirrorErrorCode('The operation was aborted due to timeout'), 'E6_READ_TIMEOUT');
  assert.equal(exchangeMirrorErrorCode('BITFINEX_AUTH_BUDGET: v2/auth/r/orders 54 sent in 60s >= routine cap 54/min'), 'BITFINEX_AUTH_BUDGET');
  assert.equal(exchangeMirrorErrorCode('Bitfinex v2/auth/r/orders: HTTP 500'), 'EXCHANGE_READ_FAILED');
});
