/**
 * Tiles 6/7 (DISLOC dsl- / DISLOC-FOI dsf-) removed 23 Oct 2026 (HM review C2):
 * a retired trade id is rejected at ingest (wake routing + approval ingest) AND at
 * placement, even with a validly signed Fly approval naming the old lane.
 */
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { test } from 'node:test';
import {
  LIVE_COPY_TILE_PREFIXES,
  RETIRED_LIVE_COPY_TILE_PREFIXES,
  approvalPermitsIngest,
  approvalPermitsRelayCopy,
  evaluateLiveCopyPlacement,
  isActiveLiveCopyTileTradeId,
  isRetiredLiveCopyTileTradeId,
} from './live-copy-approval';
import { relayWakeTradeIdRoutable, parseExecutorWakeRequest } from '../relay-executor-wake-http';
import * as svc from './signal-subscriber-execution.service';

const NOW_MS = 1_760_000_010_000;
const RETIRED = [
  ['FAMILY_DISLOC_BFX_DISLOCATION_1M', 'dsl-1791785201-4'],
  ['FAMILY_DISLOC_FOI_LONG_1M', 'dsf-1791785201-2'],
] as const;

/** Two-tile gate: Fly signs the trade's tile key (mandatory for live entries). */
function testTileKey(tradeId: string) {
  const head = String(tradeId).split('-')[0].toLowerCase();
  const code = ({ rnd: 1, gs1: 2, gb1: 3, gb2: 4, gs6: 5, dsl: 6, dsf: 7 } as Record<string, number>)[head];
  return code == null ? {} : { tile_key: head, tile_key_code: code };
}

function signed(tradeId: string, lane: string, over: Record<string, unknown> = {}) {
  const approval = {
    schema: 'fly_live_copy_approval_v1', correlation_id: tradeId, trade_id: tradeId, event: 'ORDER_PLACED',
    research_lane: lane, relay_eligible: true, eligibility_source: 'OPERATOR',
    entry_allowed: true, output_on: true, tile_live_on: true, created_at_ts: NOW_MS / 1000 - 1,
    max_margin_usd: 0.25, leverage: 100, order_type: 'LIMIT', hard_stop_bp: 35, exchange_stop_bp: 35, ...testTileKey(tradeId), ...over,
  };
  const text = JSON.stringify(approval);
  const key = createHmac('sha256', 's').update('fly-live-copy-approval-v1').digest();
  return { ...approval, signed_body: text, signature: createHmac('sha256', key).update(text).digest('hex') };
}

test('retired prefixes are not active tiles and are flagged retired', () => {
  assert.deepEqual([...RETIRED_LIVE_COPY_TILE_PREFIXES].sort(), ['dsf', 'dsl']);
  for (const [lane, tid] of RETIRED) {
    assert.equal(LIVE_COPY_TILE_PREFIXES[lane], undefined, lane);
    assert.equal(isRetiredLiveCopyTileTradeId(tid), true, tid);
    assert.equal(isRetiredLiveCopyTileTradeId(`adopt:${tid}`), true, tid);
    assert.equal(isActiveLiveCopyTileTradeId(tid), false, tid);
  }
  assert.equal(Object.values(LIVE_COPY_TILE_PREFIXES).some((p) => RETIRED_LIVE_COPY_TILE_PREFIXES.has(p)), false);
  assert.equal(isRetiredLiveCopyTileTradeId('gs1-446291153ae7'), false);
  assert.equal(isActiveLiveCopyTileTradeId('gs1-446291153ae7'), true);
});

test('ingest: retired ids are unroutable wakes and fail approval ingest', () => {
  const now = Date.now();
  for (const [lane, tid] of RETIRED) {
    assert.equal(relayWakeTradeIdRoutable(tid), false, tid);
    // Same well-formed payloads that route for an active tile (live-fix-0442 '1480' spec) are refused here.
    const close = parseExecutorWakeRequest({
      trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: tid,
      signedClose: {
        tradeId: tid, eventId: `close-${tid}`, eventSeq: 3, exitPrice: 83_900,
        exitReason: 'TIME_EXIT_60S', sourceEventAtMs: now - 400, platformReceivedAtMs: now,
      },
    });
    assert.equal(close, null, `close ${tid}`);
    const open = parseExecutorWakeRequest({
      trigger: 'POSITION_OPENED', at: new Date(now).toISOString(), tradeId: tid,
      signedOpen: { fillPrice: 83_900, sourceEventAtMs: now - 300, platformReceivedAtMs: now },
    });
    assert.equal(open, null, `open ${tid}`);
    for (const event of ['ORDER_PLACED', 'POSITION_CLOSED']) {
      assert.equal(approvalPermitsIngest(signed(tid, lane, { event }), tid, event, 's', NOW_MS), false, `${event} ${tid}`);
    }
  }
});

test('placement: a validly signed approval for a retired tile is refused (TILE_RETIRED)', () => {
  for (const [lane, tid] of RETIRED) {
    const approval = signed(tid, lane);
    assert.deepEqual(approvalPermitsRelayCopy(approval, tid, 's'), { ok: false, reason: 'TILE_RETIRED' });
    const v = evaluateLiveCopyPlacement({
      envelope: { context: { fly_live_approval: approval } }, tradeId: tid, secret: 's', accountArmed: true, nowMs: NOW_MS,
    });
    assert.deepEqual(v, { ok: false, reason: 'TILE_RETIRED' });
  }
  // Control: the same approval shape on an active tile still passes.
  const ok = evaluateLiveCopyPlacement({
    envelope: { context: { fly_live_approval: signed('gs1-abc', 'FAMILY_GS01_XV_PREMIUM_ATR_TP') } },
    tradeId: 'gs1-abc', secret: 's', accountArmed: true, nowMs: NOW_MS,
  });
  assert.equal(ok.ok, true);
});

test('DISLOC executor paths are gone; the generic prearm bound stays', () => {
  const s = svc as Record<string, unknown>;
  for (const gone of ['dislocEntryOrderMode', 'isTimeDeadlineExitTradeId', 'timeDeadlineExitEnabled', 'TIME_DEADLINE_HOLD_SEC']) {
    assert.equal(s[gone], undefined, gone);
  }
  assert.equal(svc.TIME_DEADLINE_PREARM_MS, 1_500);
});
