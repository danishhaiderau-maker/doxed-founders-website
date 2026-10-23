/**
 * #852 E6 timing (RK 24 Oct, HM review): healthy stop evidence must always be
 * < 5 s old end to end at the Fly guardian, and the fast cadence must stay well
 * under Bitfinex's authenticated REST budget (90 req/min per endpoint per IP).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  EXCHANGE_MIRROR_AUTH_GUARD_PER_MIN,
  EXCHANGE_MIRROR_BALANCE_EVERY_MS,
  EXCHANGE_MIRROR_DUE_CHECK_MS,
  EXCHANGE_MIRROR_EXPOSED_EVERY_MS,
  EXCHANGE_MIRROR_REUSE_MAX_AGE_MS,
  STOP_EVIDENCE_TIMING,
  buildExchangeMirror,
  exchangeMirrorMayRun,
  exchangeMirrorReadPlan,
  stopEvidenceWorstCaseAgeS,
} from './signal-subscriber-execution.service';
import { flyViewMirror } from './trading-agents.service';
import {
  BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN,
  bitfinexAuthCallsLastMinute,
  recentBitfinexAuthRead,
  recordBitfinexAuthCall,
  resetBitfinexAuthCallLog,
} from '../exchanges/bitfinex-api.client';

const STOP_RESIDENT_MAX_AGE_S = 5.0; // live_tile_gate.STOP_RESIDENT_MAX_AGE_S

test('E6: worst-case evidence age under normal cadence is <= 4 s (< 5 s: no false flatten)', () => {
  const worst = stopEvidenceWorstCaseAgeS();
  // 1.75 relay cadence + 0.25 due check + 1.2 reads + 0.25 write + 0.5 Fly HTTP = 3.95 s
  assert.equal(worst, 3.95);
  assert.ok(worst <= 4.0);
  assert.ok(worst < STOP_RESIDENT_MAX_AGE_S);
  assert.equal(STOP_EVIDENCE_TIMING.relayEveryS * 1000, EXCHANGE_MIRROR_EXPOSED_EVERY_MS);
  assert.equal(STOP_EVIDENCE_TIMING.dueCheckS * 1000, EXCHANGE_MIRROR_DUE_CHECK_MS);
  assert.ok(EXCHANGE_MIRROR_EXPOSED_EVERY_MS <= 2_000, 'relay stop-resident refresh <= 2 s');
  // the previous 4 s relay (1 s check) / 3 s Fly cache (+1 s tick) cadence could not meet the rule: 10.95 s
  assert.equal(stopEvidenceWorstCaseAgeS({ relayEveryS: 4, dueCheckS: 1, readS: 1.2, writeS: 0.25, flyHttpS: 0.5 + 3 + 1 }), 10.95);
});

test('E6: simulated end-to-end age stays < 5 s across every phase of the normal cadence', () => {
  // relay evidence (send time) at e; next publication once age >= 1.75 s, at a 250 ms check, after reads+write;
  // Fly fetches every 1 s tick, age = read_age_s(at response) + (now - fetched_at, stamped before the request).
  const t = STOP_EVIDENCE_TIMING;
  let maxAge = 0;
  for (let phase = 0; phase < 1; phase += 0.01) {
    let evidenceAt = 0;
    let publishedAt = t.readS + t.writeS;
    let current = { evidenceAt: -Infinity, publishedAt: -Infinity };
    void current.publishedAt;
    const pubs: Array<{ evidenceAt: number; publishedAt: number }> = [];
    for (let k = 0; k < 40; k++) {
      pubs.push({ evidenceAt, publishedAt });
      // due when age >= relayEveryS, observed at the next 250 ms check (worst: a full check late)
      const dueAt = evidenceAt + t.relayEveryS + t.dueCheckS;
      evidenceAt = dueAt;
      publishedAt = dueAt + t.readS + t.writeS;
    }
    for (let fetchAt = phase + 10; fetchAt < 60; fetchAt += 1) {
      const respAt = fetchAt + t.flyHttpS;
      for (const p of pubs) if (p.publishedAt <= respAt) current = p;
      const readAgeAtResp = respAt - current.evidenceAt;
      const flyAge = readAgeAtResp + (respAt - fetchAt);
      maxAge = Math.max(maxAge, flyAge);
    }
  }
  assert.ok(maxAge <= stopEvidenceWorstCaseAgeS() + 1e-9, `simulated ${maxAge}`);
  assert.ok(maxAge < STOP_RESIDENT_MAX_AGE_S);
});

test('E6: fly-view read_age_s is measured from the oldest read SEND time (never optimistic)', () => {
  const sent = Date.parse('2026-10-24T00:00:00.000Z');
  const mirror = buildExchangeMirror({
    orders: [], position: null, balance: null,
    readStartedAt: sent + 200, readAt: sent + 900, evidenceAt: sent,
  });
  assert.equal(mirror.read_at, new Date(sent).toISOString());
  assert.equal(flyViewMirror(mirror, sent + 3_950)?.read_age_s, 4);
});

test('E6 budget: fast mirror reuses money-tick reads, reads only when none is fresh, never past the guard', () => {
  assert.equal(BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN, 90);
  assert.equal(EXCHANGE_MIRROR_AUTH_GUARD_PER_MIN, 72);
  const quiet = { 'v2/auth/r/orders': 0 };
  assert.deepEqual(exchangeMirrorReadPlan({ ordersReuseAgeMs: 400, positionsReuseAgeMs: 900, balanceAgeMs: 2_000, callsLastMinute: {} }),
    { orders: 'REUSE', positions: 'REUSE', balance: 'REUSE' });
  assert.deepEqual(exchangeMirrorReadPlan({ ordersReuseAgeMs: EXCHANGE_MIRROR_REUSE_MAX_AGE_MS + 1, positionsReuseAgeMs: null, balanceAgeMs: EXCHANGE_MIRROR_BALANCE_EVERY_MS, callsLastMinute: quiet }),
    { orders: 'READ', positions: 'READ', balance: 'READ' });
  assert.deepEqual(exchangeMirrorReadPlan({ ordersReuseAgeMs: null, positionsReuseAgeMs: 100, balanceAgeMs: null,
    callsLastMinute: { 'v2/auth/r/orders': 72, 'v2/auth/r/wallets': 72 } }),
  { orders: 'SKIP', positions: 'REUSE', balance: 'REUSE' });
  // Fast mirror alone (no money tick to reuse): 60 / 1.75 = 34.3 orders + 34.3 positions reads/min per exposed account,
  // wallets 60 / 15 = 4/min: under the 72 guard and well under Bitfinex's 90.
  const perMin = 60_000 / EXCHANGE_MIRROR_EXPOSED_EVERY_MS;
  assert.ok(perMin < 35 && perMin < EXCHANGE_MIRROR_AUTH_GUARD_PER_MIN);
  assert.equal(60_000 / EXCHANGE_MIRROR_BALANCE_EVERY_MS, 4);
});

test('E6 budget: rolling 60 s per-endpoint counter and reusable recent reads', () => {
  resetBitfinexAuthCallLog();
  const t0 = 10_000_000;
  for (let i = 0; i < 5; i++) recordBitfinexAuthCall('v2/auth/r/orders', t0 + i * 1_000);
  recordBitfinexAuthCall('v2/auth/r/positions', t0);
  assert.deepEqual(bitfinexAuthCallsLastMinute(t0 + 5_000), { 'v2/auth/r/orders': 5, 'v2/auth/r/positions': 1 });
  assert.deepEqual(bitfinexAuthCallsLastMinute(t0 + 60_500), { 'v2/auth/r/orders': 4 });
  assert.equal(recentBitfinexAuthRead('k', 'orders', 1_000), null);
  resetBitfinexAuthCallLog();
});

test('E6: the fast stop-evidence refresh may run during a money tick; the full mirror still may not (LIVE-FIX-0741)', () => {
  const base = { destroyed: false, mirrorRunning: false, moneyTickRunning: true, lastMirrorAtMs: 0, nowMs: 1 };
  assert.equal(exchangeMirrorMayRun(base), false);
  assert.equal(exchangeMirrorMayRun({ ...base, fastExposed: true }), true);
  assert.equal(exchangeMirrorMayRun({ ...base, fastExposed: true, mirrorRunning: true }), false);
});
