import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ACCOUNT_NOT_FLAT,
  LIVE_SKIPPED_MUTEX,
  LIVE_TILE_KEY_CODES,
  RESERVED_LIVE_TILE_KEY_CODES,
  decodeTileClientOrderId,
  encodeTileClientOrderId,
  liveEntryAdmission,
  tileKeyCode,
  tileKeyForTradeId,
  UNTAGGED_ORDER_REFUSED,
  untaggedOrderRefusal,
} from './live-tile-gate';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { assertTileTaggedEntry } from '../exchanges/bitfinex-api.client';
import { LIVE_COPY_TILE_PREFIXES, approvalTileMatchesTrade } from './live-copy-approval';

test('every live-copy tile prefix has a stable tile code; codes are unique and never reuse reserved ones', () => {
  for (const prefix of Object.values(LIVE_COPY_TILE_PREFIXES)) {
    assert.ok(tileKeyCode(prefix) != null, `prefix ${prefix} has no tile code`);
  }
  const codes = [...Object.values(LIVE_TILE_KEY_CODES), ...Object.values(RESERVED_LIVE_TILE_KEY_CODES)];
  assert.equal(new Set(codes).size, codes.length);
  for (const c of codes) assert.ok(c >= 1 && c <= 127);
  assert.equal(LIVE_TILE_KEY_CODES.gs1, 2);
  assert.equal(RESERVED_LIVE_TILE_KEY_CODES.gs6, 5);
  assert.equal(RESERVED_LIVE_TILE_KEY_CODES.gf3l, 9);
});

test('tile key from trade id ignores adopt/relink wrappers and unknown prefixes', () => {
  assert.equal(tileKeyForTradeId('gs1-abc'), 'gs1');
  assert.equal(tileKeyForTradeId('ADOPT:gb2-x'), null);  // retired/reserved
  assert.equal(tileKeyForTradeId('relink:rnd-1'), 'rnd');
  assert.equal(tileKeyForTradeId('dsl-1'), null);
  assert.equal(tileKeyForTradeId('gs1-'), null);
  assert.equal(tileKeyForTradeId(null), null);
});

test('cid round-trips the tile, stays a positive int32, and keeps hashes distinct within a tile', () => {
  for (const [key] of Object.entries(LIVE_TILE_KEY_CODES)) {
    for (const h of [0, 1, 0x1fffff, 0x7fffffff, 123456789]) {
      const cid = encodeTileClientOrderId(key, h);
      assert.ok(Number.isInteger(cid) && cid > 0 && cid <= 0x7fffffff);
      assert.equal(decodeTileClientOrderId(cid), key);
      assert.equal(decodeTileClientOrderId(String(cid)), key);
    }
  }
  assert.notEqual(encodeTileClientOrderId('gs1', 1), encodeTileClientOrderId('gs1', 2));
  assert.equal(encodeTileClientOrderId('gs1', 7), encodeTileClientOrderId('gs1', 7));
  assert.throws(() => encodeTileClientOrderId('gs6', 7));
  assert.throws(() => encodeTileClientOrderId('dsl', 1));
});

test('legacy / foreign cids decode to no tile', () => {
  assert.equal(decodeTileClientOrderId(null), null);
  assert.equal(decodeTileClientOrderId(0), null);
  assert.equal(decodeTileClientOrderId(-5), null);
  assert.equal(decodeTileClientOrderId(0x80000000), null);
  assert.equal(decodeTileClientOrderId(0x10000000 | (2 << 21)), null); // wrong marker
  assert.equal(decodeTileClientOrderId((0b101 << 28) | (6 << 21)), null); // reserved code
});

test('per-entry admission: mutex then net=0', () => {
  assert.deepEqual(liveEntryAdmission({ otherLiveParticipants: 0, positionQty: null }), { ok: true });
  assert.deepEqual(liveEntryAdmission({ otherLiveParticipants: 0, positionQty: 0 }), { ok: true });
  assert.deepEqual(liveEntryAdmission({ otherLiveParticipants: 1, positionQty: 0 }), { ok: false, reason: LIVE_SKIPPED_MUTEX });
  assert.deepEqual(liveEntryAdmission({ otherLiveParticipants: 0, positionQty: -0.0001 }), { ok: false, reason: ACCOUNT_NOT_FLAT });
  assert.deepEqual(liveEntryAdmission({ otherLiveParticipants: 0, positionQty: Number.NaN }), { ok: false, reason: ACCOUNT_NOT_FLAT });
});

test('approval tile key is MANDATORY for a live entry (RK) and must match the trade tile; continuations may be untagged', () => {
  const lane = Object.entries(LIVE_COPY_TILE_PREFIXES).find(([, p]) => p === 'gs1')?.[0];
  assert.ok(lane);
  const base = { research_lane: lane, trade_id: 'gs1-x1' } as Record<string, unknown>;
  assert.equal(approvalTileMatchesTrade(base as never, 'gs1-x1'), false);   // untagged entry refused (default)
  assert.equal(approvalTileMatchesTrade(base as never, 'gs1-x1', { requireTileKey: true }), false);
  assert.equal(approvalTileMatchesTrade(base as never, 'gs1-x1', { requireTileKey: false }), true);   // exit continuation
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key: 'gb2', tile_key_code: 4 } as never, 'gs1-x1', { requireTileKey: false }), false);
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key: 'gs1', tile_key_code: 2 } as never, 'gs1-x1'), true);
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key: 'gb2', tile_key_code: 4 } as never, 'gs1-x1'), false);
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key: 'gs1', tile_key_code: 5 } as never, 'gs1-x1'), false);
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key_code: 2 } as never, 'gs1-x1'), false);
});

test('RK 02:25: only reduce-only closing orders may be untagged; every other order needs a tile-keyed cid', () => {
  const tagged = encodeTileClientOrderId('gs1', 12345);
  // reduce-only closes (stops, market close, flatten) may carry any cid or none
  assert.equal(untaggedOrderRefusal({ clientOrderId: undefined, reduceOnly: true }), null);
  assert.equal(untaggedOrderRefusal({ clientOrderId: 987654, reduceOnly: true }), null);
  // non-reduce-only: tagged passes, legacy/foreign/missing/stop-style cids are refused
  assert.equal(untaggedOrderRefusal({ clientOrderId: tagged, reduceOnly: false }), null);
  for (const cid of [undefined, null, 0, -5, 987654, 0x7fffffff, 'abc', encodeTileClientOrderId('gs1', 1) & ~(0b111 << 28)]) {
    assert.equal(untaggedOrderRefusal({ clientOrderId: cid as never, reduceOnly: false }), UNTAGGED_ORDER_REFUSED, String(cid));
  }
  // a reserved/retired tile code (dsl=6) is not a live tile
  const dsl = (0b101 << 28) | (6 << 21) | 7;
  assert.equal(untaggedOrderRefusal({ clientOrderId: dsl, reduceOnly: false }), UNTAGGED_ORDER_REFUSED);
  // exchange-client last line of defence throws before any request for an untagged entry
  assert.throws(() => assertTileTaggedEntry('limit entry', 987654), /UNTAGGED_ORDER_REFUSED/);
  assert.throws(() => assertTileTaggedEntry('market entry', undefined), /UNTAGGED_ORDER_REFUSED/);
  assert.doesNotThrow(() => assertTileTaggedEntry('limit entry', tagged));
});

test('RK 02:25: untagged guard runs before every non-reduce-only submit (hire, chase, catch-up, exchange client)', () => {
  const svc = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const before = (guard: string, submit: string, from: number) => {
    const g = svc.indexOf(guard, from);
    const s = svc.indexOf(submit, from);
    assert.ok(g > 0 && s > 0 && g < s, `${guard} must precede ${submit}`);
    return s;
  };
  const hire = svc.indexOf('entryClientOrderId = clientOrderId;');
  before("untaggedOrderRefusal({ clientOrderId, reduceOnly: false })", 'this.activeTrading.submitLimitOrder(creds, {', hire);
  const chase = svc.indexOf('const chaseUntagged = untaggedOrderRefusal');
  assert.ok(chase > 0);
  before('const chaseUntagged', 'this.activeTrading.submitLimitOrder(creds, {', chase - 2000);
  const catchup = svc.indexOf('const catchupUntagged = untaggedOrderRefusal');
  assert.ok(catchup > 0);
  before('const catchupUntagged', 'this.activeTrading.submitMarketEntry(creds, {', catchup - 2000);
  const client = readFileSync(join(__dirname, '../exchanges/bitfinex-api.client.ts'), 'utf8');
  for (const m of ['async submitLimitOrder(', 'async submitMarketEntry(']) {
    const at = client.indexOf(m);
    const body = client.slice(at, client.indexOf("bitfinexAuthPost(creds, 'v2/auth/w/order/submit'", at));
    assert.ok(body.includes('assertTileTaggedEntry('), `${m} must assert a tile-tagged cid before submit`);
  }
});
