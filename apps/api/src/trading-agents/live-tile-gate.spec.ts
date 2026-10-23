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
} from './live-tile-gate';
import { LIVE_COPY_TILE_PREFIXES, approvalTileMatchesTrade } from './live-copy-approval';

test('every live-copy tile prefix has a stable tile code; codes are unique and never reuse reserved ones', () => {
  for (const prefix of Object.values(LIVE_COPY_TILE_PREFIXES)) {
    assert.ok(tileKeyCode(prefix) != null, `prefix ${prefix} has no tile code`);
  }
  const codes = [...Object.values(LIVE_TILE_KEY_CODES), ...Object.values(RESERVED_LIVE_TILE_KEY_CODES)];
  assert.equal(new Set(codes).size, codes.length);
  for (const c of codes) assert.ok(c >= 1 && c <= 127);
  assert.equal(LIVE_TILE_KEY_CODES.gs1, 2);
  assert.equal(LIVE_TILE_KEY_CODES.gs6, 5);
});

test('tile key from trade id ignores adopt/relink wrappers and unknown prefixes', () => {
  assert.equal(tileKeyForTradeId('gs1-abc'), 'gs1');
  assert.equal(tileKeyForTradeId('ADOPT:gb2-x'), 'gb2');
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
  assert.notEqual(encodeTileClientOrderId('gs1', 7), encodeTileClientOrderId('gs6', 7));
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

test('approval tile key must match the trade tile when present; absent is allowed (pre-gate Fly)', () => {
  const lane = Object.entries(LIVE_COPY_TILE_PREFIXES).find(([, p]) => p === 'gs1')?.[0];
  assert.ok(lane);
  const base = { research_lane: lane, trade_id: 'gs1-x1' } as Record<string, unknown>;
  assert.equal(approvalTileMatchesTrade(base as never, 'gs1-x1'), true);
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key: 'gs1', tile_key_code: 2 } as never, 'gs1-x1'), true);
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key: 'gs6', tile_key_code: 5 } as never, 'gs1-x1'), false);
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key: 'gs1', tile_key_code: 5 } as never, 'gs1-x1'), false);
  assert.equal(approvalTileMatchesTrade({ ...base, tile_key_code: 2 } as never, 'gs1-x1'), false);
});
