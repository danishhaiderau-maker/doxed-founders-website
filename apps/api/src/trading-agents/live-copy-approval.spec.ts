import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { test } from 'node:test';
import {
  LIVE_COPY_TILE_PREFIXES,
  approvalPermitsIngest,
  evaluateLiveCopyPlacement,
  exchangeStopCapBp,
  liquidationDistanceBp,
  liveCopyAccountArmed,
  LiveCopyRejectRing,
  signLiveExecutionReport,
  verifyFlyLiveCopyApproval,
  verifyFlyViewSignature,
} from './live-copy-approval';

/** Signed by Fly's live_copy_control.sign_approval (Python) with 'cross-lang-secret'. */
const PY_SIGNED = {"bot_instance_id": null, "continuation": false, "correlation_id": "gs-x1", "created_at_ts": 1760000002.125, "eligibility_source": "OPERATOR", "entry_allowed": true, "event": "ORDER_PLACED", "exchange_stop_bp": 35.0, "exchange_stop_cap_applied": true, "exchange_stop_cap_bp": 35.0, "exchange_stop_inside_tile_hard_stop": false, "exchange_stop_never_moved": true, "exchange_stop_requested_bp": 60.0, "exchange_stop_role": "CATASTROPHE_BACKUP", "hard_stop_bp": 35.0, "leverage": 100, "liquidation_bp": 50.0, "max_margin_usd": 0.25, "order_type": "LIMIT", "output_on": true, "output_on_at_ts": 1760000000.5, "policy_signature": null, "reasons": [], "relay_eligible": true, "research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "schema": "fly_live_copy_approval_v1", "signal_at_ts": 1760000001.0, "tile_allow_ts": 1760000001.25, "tile_live_on": true, "trade_approved_at_ts": 1760000002.125, "trade_id": "gs-x1", "signed_body": "{\"bot_instance_id\":null,\"continuation\":false,\"correlation_id\":\"gs-x1\",\"created_at_ts\":1760000002.125,\"eligibility_source\":\"OPERATOR\",\"entry_allowed\":true,\"event\":\"ORDER_PLACED\",\"exchange_stop_bp\":35.0,\"exchange_stop_cap_applied\":true,\"exchange_stop_cap_bp\":35.0,\"exchange_stop_inside_tile_hard_stop\":false,\"exchange_stop_never_moved\":true,\"exchange_stop_requested_bp\":60.0,\"exchange_stop_role\":\"CATASTROPHE_BACKUP\",\"hard_stop_bp\":35.0,\"leverage\":100,\"liquidation_bp\":50.0,\"max_margin_usd\":0.25,\"order_type\":\"LIMIT\",\"output_on\":true,\"output_on_at_ts\":1760000000.5,\"policy_signature\":null,\"reasons\":[],\"relay_eligible\":true,\"research_lane\":\"FAMILY_GS01_XV_PREMIUM_ATR_TP\",\"schema\":\"fly_live_copy_approval_v1\",\"signal_at_ts\":1760000001.0,\"tile_allow_ts\":1760000001.25,\"tile_live_on\":true,\"trade_approved_at_ts\":1760000002.125,\"trade_id\":\"gs-x1\"}", "signature": "4561b45c86833251f5480cc4c3bd663972dd71b34445d8766ad536358a1dedaf"} as Record<string, unknown>;
const NOW_MS = 1_760_000_010_000;

test('verifies an approval signed by the Fly (Python) signer', () => {
  const v = verifyFlyLiveCopyApproval(PY_SIGNED, 'cross-lang-secret', { tradeId: 'gs-x1', event: 'ORDER_PLACED', nowMs: NOW_MS });
  assert.equal(v.ok, true);
  if (v.ok) {
    assert.equal(v.approval.exchange_stop_bp, 35);
    assert.equal(v.approval.liquidation_bp, 50);
    assert.equal(v.approval.order_type, 'LIMIT');
    assert.equal(v.approval.eligibility_source, 'OPERATOR');
  }
});

test('refuses unsigned, wrong-key, tampered, mismatched and stale approvals', () => {
  const exp = { tradeId: 'gs-x1', event: 'ORDER_PLACED', nowMs: NOW_MS };
  const { signature: _s, ...unsigned } = PY_SIGNED;
  assert.deepEqual(verifyFlyLiveCopyApproval(unsigned, 'cross-lang-secret', exp), { ok: false, reason: 'APPROVAL_UNSIGNED' });
  assert.equal(verifyFlyLiveCopyApproval(PY_SIGNED, 'other', exp).ok, false);
  assert.equal(verifyFlyLiveCopyApproval(null, 'cross-lang-secret', exp).ok, false);
  assert.equal(verifyFlyLiveCopyApproval(PY_SIGNED, '', exp).ok, false);
  const tampered = { ...PY_SIGNED, signed_body: String(PY_SIGNED.signed_body).replace('"max_margin_usd":0.25', '"max_margin_usd":25') };
  assert.equal(verifyFlyLiveCopyApproval(tampered, 'cross-lang-secret', exp).ok, false);
  assert.equal(verifyFlyLiveCopyApproval(PY_SIGNED, 'cross-lang-secret', { ...exp, tradeId: 'other' }).ok, false);
  assert.equal(verifyFlyLiveCopyApproval(PY_SIGNED, 'cross-lang-secret', { ...exp, event: 'LIMIT_UPDATED' }).ok, false);
  assert.deepEqual(
    verifyFlyLiveCopyApproval(PY_SIGNED, 'cross-lang-secret', { ...exp, nowMs: NOW_MS + 600_000 }),
    { ok: false, reason: 'APPROVAL_STALE' },
  );
});

test('execution report signature matches the Fly verifier domain', () => {
  const raw = JSON.stringify({ a: 1 });
  const key = createHmac('sha256', 's').update('railway-live-execution-report-v1').digest();
  assert.equal(signLiveExecutionReport(raw, 's'), `sha256=${createHmac('sha256', key).update(raw).digest('hex')}`);
  assert.equal(signLiveExecutionReport(raw, ''), null);
});

test('fly-view GET signature: path + ts bound, 60 s window', () => {
  const key = createHmac('sha256', 's').update('fly-website-state-v1').digest();
  const path = '/api/trading-agents/conservative-btc/live-copy/fly-view';
  const ts = String(NOW_MS / 1000);
  const sig = createHmac('sha256', key).update(`GET\n${path}\n${ts}`).digest('hex');
  assert.equal(verifyFlyViewSignature(path, ts, sig, 's', NOW_MS), true);
  assert.equal(verifyFlyViewSignature(path + 'x', ts, sig, 's', NOW_MS), false);
  assert.equal(verifyFlyViewSignature(path, ts, sig, 's', NOW_MS + 120_000), false);
  assert.equal(verifyFlyViewSignature(path, ts, undefined, 's', NOW_MS), false);
});

test('reject ring is bounded', () => {
  const r = new LiveCopyRejectRing(3);
  for (let i = 0; i < 5; i++) r.push('X', 't', 'ORDER_PLACED', 1000 + i);
  assert.equal(r.since(0).length, 3);
  assert.equal(r.since(1004).length, 1);
});

function signed(tradeId: string, secret: string, over: Record<string, unknown> = {}) {
  const approval = {
    schema: 'fly_live_copy_approval_v1', correlation_id: tradeId, trade_id: tradeId, event: 'ORDER_PLACED',
    research_lane: 'FAMILY_GS01_XV_PREMIUM_ATR_TP', relay_eligible: true, eligibility_source: 'OPERATOR',
    entry_allowed: true, output_on: true, tile_live_on: true, created_at_ts: NOW_MS / 1000 - 1,
    max_margin_usd: 0.25, leverage: 100, order_type: 'LIMIT', hard_stop_bp: 35, exchange_stop_bp: 35, ...over,
  };
  const text = JSON.stringify(approval);
  const key = createHmac('sha256', secret).update('fly-live-copy-approval-v1').digest();
  return { ...approval, signed_body: text, signature: createHmac('sha256', key).update(text).digest('hex') };
}

test('Bitfinex 100x isolated liquidation distance and backup-stop cap', () => {
  assert.equal(liquidationDistanceBp(100), 50);
  assert.equal(exchangeStopCapBp(100), 35);
  assert.equal(liquidationDistanceBp(0), 0);
});

test('placement gate: all 16 combinations of output/tile/eligible/armed, only all-on passes', () => {
  let passes = 0;
  for (let mask = 0; mask < 16; mask += 1) {
    const approval = signed('gs1-abc', 's', {
      output_on: Boolean(mask & 1), tile_live_on: Boolean(mask & 2), relay_eligible: Boolean(mask & 4),
      eligibility_source: mask & 4 ? 'OPERATOR' : 'NONE',
    });
    const v = evaluateLiveCopyPlacement({
      envelope: { context: { fly_live_approval: approval } }, tradeId: 'gs1-abc', secret: 's',
      accountArmed: Boolean(mask & 8), nowMs: NOW_MS,
    });
    assert.equal(v.ok, mask === 15, `mask=${mask}`);
    if (v.ok) {
      passes += 1;
      assert.equal(v.exchangeStopBp, 35);
      assert.equal(v.correlationId, 'gs1-abc');
    }
  }
  assert.equal(passes, 1);
});

test('placement gate refuses missing approval, stale approval and tile/trade mismatch', () => {
  const base = { tradeId: 'gs1-abc', secret: 's', accountArmed: true, nowMs: NOW_MS };
  assert.deepEqual(evaluateLiveCopyPlacement({ ...base, envelope: { context: {} } }), { ok: false, reason: 'APPROVAL_MISSING' });
  assert.equal(evaluateLiveCopyPlacement({ ...base, envelope: { context: { fly_live_approval: signed('gs1-abc', 's', { created_at_ts: NOW_MS / 1000 - 400 }) } } }).ok, false);
  assert.equal(evaluateLiveCopyPlacement({ ...base, envelope: { context: { fly_live_approval: signed('gs1-abc', 's', { research_lane: 'FAMILY_FADE_POOL' }) } } }).ok, false);
  assert.equal(evaluateLiveCopyPlacement({ ...base, envelope: { context: { fly_live_approval: signed('gs1-abc', 's', { exchange_stop_bp: 36 }) } } }).ok, false);
  assert.equal(evaluateLiveCopyPlacement({ ...base, envelope: { context: { fly_live_approval: signed('gs1-abc', 's', { eligibility_source: 'NONE' }) } } }).ok, false);
});

test('ingest routing: continuations of an approved trade flow even after the tile switch is OFF', () => {
  const close = signed('gs1-abc', 's', { event: 'POSITION_CLOSED', entry_allowed: false, tile_live_on: false, relay_eligible: false });
  assert.equal(approvalPermitsIngest(close, 'gs1-abc', 'POSITION_CLOSED', 's', NOW_MS), true);
  assert.equal(approvalPermitsIngest(close, 'gs1-abc', 'ORDER_PLACED', 's', NOW_MS), false);
  assert.equal(approvalPermitsIngest(signed('gs1-abc', 's', { tile_live_on: false }), 'gs1-abc', 'ORDER_PLACED', 's', NOW_MS), false);
  assert.equal(approvalPermitsIngest(signed('xyz-abc', 's', { trade_id: 'xyz-abc' }), 'xyz-abc', 'ORDER_PLACED', 's', NOW_MS), false);
});

test('account armed means ACTIVE, real exchange and explicit relayArmedAt (no legacy fallback)', () => {
  const armedAt = new Date(NOW_MS).toISOString();
  assert.equal(liveCopyAccountArmed({ status: 'ACTIVE', exchangeProvider: 'bitfinex', dashboardState: { relayArmedAt: armedAt } }), true);
  assert.equal(liveCopyAccountArmed({ status: 'PAUSED', exchangeProvider: 'bitfinex', dashboardState: { relayArmedAt: armedAt } }), false);
  assert.equal(liveCopyAccountArmed({ status: 'ACTIVE', exchangeProvider: 'paper', dashboardState: { relayArmedAt: armedAt } }), false);
  assert.equal(liveCopyAccountArmed({ status: 'ACTIVE', exchangeProvider: 'bitfinex', dashboardState: { realTradingConfirmedAt: armedAt } }), false);
  assert.equal(liveCopyAccountArmed(null), false);
});

test('tile prefix map covers the active Fly tiles', () => {
  assert.equal(LIVE_COPY_TILE_PREFIXES.FAMILY_GS01_XV_PREMIUM_ATR_TP, 'gs1');
  assert.equal(Object.keys(LIVE_COPY_TILE_PREFIXES).length, 12);
});
