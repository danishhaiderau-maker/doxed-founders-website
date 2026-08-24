import assert from 'node:assert/strict';
import test from 'node:test';
import {
  assessBitfinexLiveCopySizingReadiness,
  missingBitfinexVenueEvidenceReadiness,
} from './bitfinex-live-copy-readiness';

const constraints = {
  symbol: 'tBTCF0:USTF0', minQtyBtc: 0.00004, maxQtyBtc: 100,
  priceSignificantDigits: 5, amountDecimals: 8,
  observedAt: '2026-08-24T00:00:00.000Z',
  source: 'BITFINEX_PUBLIC_FUTURES_CONFIG' as const,
};

test('missing venue constraints and authenticated acceptance remain fail closed', () => {
  const report = missingBitfinexVenueEvidenceReadiness();
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
