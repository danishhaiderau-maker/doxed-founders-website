import assert from 'node:assert/strict';
import test from 'node:test';
import { computeStopPrice } from '@dcf/utils';
import { parseBitfinexBtcPerpConstraints } from '../exchanges/bitfinex-api.client';
import {
  assessBitfinexLiveCopySizingReadiness,
  missingBitfinexVenueEvidenceReadiness,
} from './bitfinex-live-copy-readiness';
import {
  MAX_SIGNED_COPY_MARGIN_PER_LEG_USD,
  resolveEffectiveStopLossMarginPct,
  resolveExactShowcaseEntryQty,
} from './signal-subscriber-execution.service';

/** Public `conf/pub:info:pair:futures` row captured 2026-09-29T23:21Z. */
const LIVE_FUTURES_CONFIG = [[
  ['BTCF0:USTF0', [1562164542332, null, null, '0.00004', '100.0', null, null, null, 0.01, 0.005]],
]];
const LIVE_REFERENCE_PRICE = 83_800;

const constraints = {
  symbol: 'tBTCF0:USTF0', minQtyBtc: 0.00004, maxQtyBtc: 100,
  priceSignificantDigits: 5, amountDecimals: 8,
  observedAt: '2026-08-24T00:00:00.000Z',
  source: 'BITFINEX_PUBLIC_FUTURES_CONFIG' as const,
};

test('missing venue constraints and authenticated acceptance remain fail closed', () => {
  const report = missingBitfinexVenueEvidenceReadiness();
  assert.equal(report.status, 'UNKNOWN_NOT_PROVEN');
  assert.equal(report.ready, false);
  assert.ok(report.blockers.includes('VENUE_CONSTRAINTS_EVIDENCE_MISSING'));
  assert.ok(report.blockers.includes('AUTHENTICATED_VENUE_ACCEPTANCE_RECEIPT_MISSING'));
});

test('exact authenticated accepted sizing reconciles offline', () => {
  const requestedLimitPrice = 64_000;
  const requestedQtyBtc = 0.00039;
  const acceptedNotionalUsd = requestedQtyBtc * requestedLimitPrice;
  const report = assessBitfinexLiveCopySizingReadiness({
    requestedMarginUsd: 0.25, requestedQtyBtc, requestedLimitPrice, leverage: 100,
    constraints,
    acceptance: {
      authenticated: true, orderId: 123, requestedQtyBtc, acceptedQtyBtc: requestedQtyBtc,
      acceptedLimitPrice: requestedLimitPrice, leverage: 100,
      acceptedNotionalUsd, acceptedMarginUsd: acceptedNotionalUsd / 100,
      activeOrdersReconciled: true, positionsReconciled: true, executionsReconciled: true,
    },
  });
  assert.deepEqual(report.blockers, []);
  assert.equal(report.status, 'ACCEPTED_PROVEN');
  assert.equal(report.ready, true);
});

test('venue drift and unreconciled acceptance are explicit blockers', () => {
  const report = assessBitfinexLiveCopySizingReadiness({
    requestedMarginUsd: 0.25, requestedQtyBtc: 0.00039, requestedLimitPrice: 64_000, leverage: 100,
    constraints: { ...constraints, minQtyBtc: 0.001 },
    acceptance: {
      authenticated: true, orderId: 123, requestedQtyBtc: 0.00039, acceptedQtyBtc: 0.0004,
      acceptedLimitPrice: 64_000, leverage: 100,
      acceptedNotionalUsd: 1, acceptedMarginUsd: 0.3,
      activeOrdersReconciled: false, positionsReconciled: false, executionsReconciled: false,
    },
  });
  assert.equal(report.ready, false);
  assert.ok(report.blockers.includes('VENUE_MIN_QTY_DRIFT'));
  assert.ok(report.blockers.includes('VENUE_ACCEPTED_MARGIN_EXCEEDS_CAP'));
  assert.ok(report.blockers.includes('EXECUTIONS_RECONCILIATION_MISSING'));
});

test('live-test $0.20-$0.25 at 100x floors to venue precision and never exceeds the margin cap', () => {
  const venue = parseBitfinexBtcPerpConstraints(LIVE_FUTURES_CONFIG);
  assert.ok(venue);
  for (const [marginUsd, expectedQty] of [[0.25, 0.00029], [0.2, 0.00023]] as const) {
    const exactQtyBtc = marginUsd * 100 / LIVE_REFERENCE_PRICE;
    const result = resolveExactShowcaseEntryQty({
      exactQtyBtc, maxMarginUsd: marginUsd, leverage: 100,
      limitPrice: LIVE_REFERENCE_PRICE, minQtyBtc: venue.minQtyBtc,
    });
    assert.ok(result.ok, JSON.stringify(result));
    assert.equal(result.qty, expectedQty);
    assert.ok(result.qty <= exactQtyBtc);
    assert.ok(result.qty >= venue.minQtyBtc);
    assert.ok(result.requiredMarginUsd <= marginUsd);
  }
});

test('below the venue minimum the exact entry fails closed instead of rounding up', () => {
  const result = resolveExactShowcaseEntryQty({
    exactQtyBtc: 0.0000399, maxMarginUsd: 0.25, leverage: 100,
    limitPrice: LIVE_REFERENCE_PRICE, minQtyBtc: 0.00004,
  });
  assert.deepEqual(result, { ok: false, reason: 'BELOW_EXCHANGE_MIN_QTY' });
});

test('venue minimum costing more than the $0.25 cap fails closed instead of expanding margin', () => {
  // At $700k the 0.00004 BTC minimum needs $0.28 margin at 100x.
  const result = resolveExactShowcaseEntryQty({
    exactQtyBtc: 0.00004, maxMarginUsd: 0.25, leverage: 100,
    limitPrice: 700_000, minQtyBtc: 0.00004,
  });
  assert.deepEqual(result, { ok: false, reason: 'SOURCE_QTY_EXCEEDS_SUBSCRIBER_CAP' });
});

test('any per-leg margin input above $0.25 is rejected before sizing', () => {
  assert.equal(MAX_SIGNED_COPY_MARGIN_PER_LEG_USD, 0.25);
  for (const maxMarginUsd of [0.2500001, 0.26, 20]) {
    assert.deepEqual(
      resolveExactShowcaseEntryQty({
        exactQtyBtc: 0.00001, maxMarginUsd, leverage: 100,
        limitPrice: LIVE_REFERENCE_PRICE, minQtyBtc: 0.00004,
      }),
      { ok: false, reason: 'MARGIN_CEILING_EXCEEDED' },
    );
  }
});

test('real exchange stop always sits inside the 100x isolated liquidation estimate', () => {
  const venue = parseBitfinexBtcPerpConstraints(LIVE_FUTURES_CONFIG);
  assert.ok(venue);
  const leverage = 100;
  // Isolated liquidation when equity falls to maintenance margin:
  // adverse move = posted margin fraction (1/leverage) - maintenance fraction.
  const liquidationMoveFraction = 1 / leverage - venue.maintenanceMarginFraction;
  assert.ok(Math.abs(liquidationMoveFraction - 0.005) < 1e-12);
  const roundTripFeeAllowance = 0.002;
  for (const requested of [undefined, -13, -40, -99]) {
    const stopMarginPct = resolveEffectiveStopLossMarginPct(requested, { mirrorMode: true, simActive: false });
    assert.ok(stopMarginPct >= -13);
    for (const direction of ['LONG', 'SHORT'] as const) {
      const entry = LIVE_REFERENCE_PRICE;
      const stop = computeStopPrice(entry, direction, stopMarginPct, leverage);
      const stopMove = Math.abs(stop - entry) / entry;
      const liquidation = direction === 'LONG'
        ? entry * (1 - liquidationMoveFraction)
        : entry * (1 + liquidationMoveFraction);
      assert.ok(stopMove + roundTripFeeAllowance < liquidationMoveFraction, `${direction} ${stopMarginPct}`);
      assert.ok(direction === 'LONG' ? stop > liquidation : stop < liquidation);
    }
  }
});
