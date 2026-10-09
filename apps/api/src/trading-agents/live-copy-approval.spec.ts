import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { test } from 'node:test';
import {
  LiveCopyRejectRing,
  signLiveExecutionReport,
  verifyFlyLiveCopyApproval,
  verifyFlyViewSignature,
} from './live-copy-approval';

/** Signed by Fly's live_copy_control.sign_approval (Python) with 'cross-lang-secret'. */
const PY_SIGNED = {"bot_instance_id": null, "continuation": false, "correlation_id": "gs-x1", "created_at_ts": 1760000002.125, "eligibility_source": "OPERATOR", "entry_allowed": true, "event": "ORDER_PLACED", "exchange_stop_bp": 45.0, "hard_stop_bp": 40.0, "leverage": 100, "max_margin_usd": 0.25, "order_type": "LIMIT", "output_on": true, "output_on_at_ts": 1760000000.5, "policy_signature": null, "reasons": [], "relay_eligible": true, "research_lane": "FAMILY_X", "schema": "fly_live_copy_approval_v1", "signal_at_ts": 1760000001.0, "tile_allow_ts": 1760000001.25, "tile_live_on": true, "trade_approved_at_ts": 1760000002.125, "trade_id": "gs-x1", "signed_body": "{\"bot_instance_id\":null,\"continuation\":false,\"correlation_id\":\"gs-x1\",\"created_at_ts\":1760000002.125,\"eligibility_source\":\"OPERATOR\",\"entry_allowed\":true,\"event\":\"ORDER_PLACED\",\"exchange_stop_bp\":45.0,\"hard_stop_bp\":40.0,\"leverage\":100,\"max_margin_usd\":0.25,\"order_type\":\"LIMIT\",\"output_on\":true,\"output_on_at_ts\":1760000000.5,\"policy_signature\":null,\"reasons\":[],\"relay_eligible\":true,\"research_lane\":\"FAMILY_X\",\"schema\":\"fly_live_copy_approval_v1\",\"signal_at_ts\":1760000001.0,\"tile_allow_ts\":1760000001.25,\"tile_live_on\":true,\"trade_approved_at_ts\":1760000002.125,\"trade_id\":\"gs-x1\"}", "signature": "1f0179075d322b212f2382f1e65bd31f59db4ba26ff675fbb74cd042074c7eff"} as Record<string, unknown>;
const NOW_MS = 1_760_000_010_000;

test('verifies an approval signed by the Fly (Python) signer', () => {
  const v = verifyFlyLiveCopyApproval(PY_SIGNED, 'cross-lang-secret', { tradeId: 'gs-x1', event: 'ORDER_PLACED', nowMs: NOW_MS });
  assert.equal(v.ok, true);
  if (v.ok) {
    assert.equal(v.approval.exchange_stop_bp, 45);
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
