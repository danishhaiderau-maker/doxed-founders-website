export const BITFINEX_BTC_PERP_EXPECTED_MIN_QTY = 0.00004;
export const BITFINEX_BTC_PERP_EXPECTED_MAX_QTY = 100;
export const BITFINEX_PRICE_SIGNIFICANT_DIGITS = 5;
export const BITFINEX_AMOUNT_DECIMALS = 8;

export type BitfinexVenueSizingConstraints = {
  symbol: string;
  minQtyBtc: number;
  maxQtyBtc: number;
  priceSignificantDigits: number;
  amountDecimals: number;
  observedAt: string;
  source: 'BITFINEX_PUBLIC_FUTURES_CONFIG';
};

export type BitfinexAcceptedSizingReceipt = {
  authenticated: true;
  orderId: number;
  requestedQtyBtc: number;
  acceptedQtyBtc: number;
  acceptedLimitPrice: number;
  leverage: number;
  acceptedNotionalUsd: number;
  acceptedMarginUsd: number;
  activeOrdersReconciled: true;
  positionsReconciled: true;
  executionsReconciled: true;
};

export function assessBitfinexLiveCopySizingReadiness(input: {
  requestedMarginUsd: number;
  requestedQtyBtc: number;
  requestedLimitPrice: number;
  leverage: number;
  constraints?: BitfinexVenueSizingConstraints | null;
  acceptance?: BitfinexAcceptedSizingReceipt | null;
}) {
  const blockers: string[] = [];
  const constraints = input.constraints;
  if (!constraints) {
    blockers.push('VENUE_CONSTRAINTS_EVIDENCE_MISSING');
  } else {
    if (constraints.symbol !== 'tBTCF0:USTF0') blockers.push('VENUE_SYMBOL_MISMATCH');
    if (constraints.minQtyBtc !== BITFINEX_BTC_PERP_EXPECTED_MIN_QTY) blockers.push('VENUE_MIN_QTY_DRIFT');
    if (constraints.maxQtyBtc !== BITFINEX_BTC_PERP_EXPECTED_MAX_QTY) blockers.push('VENUE_MAX_QTY_DRIFT');
    if (constraints.priceSignificantDigits !== BITFINEX_PRICE_SIGNIFICANT_DIGITS) blockers.push('VENUE_PRICE_PRECISION_DRIFT');
    if (constraints.amountDecimals !== BITFINEX_AMOUNT_DECIMALS) blockers.push('VENUE_AMOUNT_PRECISION_DRIFT');
    if (!(input.requestedQtyBtc >= constraints.minQtyBtc && input.requestedQtyBtc <= constraints.maxQtyBtc)) {
      blockers.push('REQUESTED_QTY_OUTSIDE_VENUE_LIMITS');
    }
  }
  if (!(input.requestedMarginUsd > 0 && input.requestedMarginUsd <= 0.25)) blockers.push('REQUESTED_MARGIN_OUT_OF_RANGE');
  if (!(input.requestedLimitPrice > 0)) blockers.push('REQUESTED_LIMIT_PRICE_INVALID');
  if (input.leverage !== 100) blockers.push('REQUESTED_LEVERAGE_MISMATCH');
  const acceptance = input.acceptance;
  if (!acceptance) {
    blockers.push('AUTHENTICATED_VENUE_ACCEPTANCE_RECEIPT_MISSING');
  } else {
    if (!(acceptance.orderId > 0) || acceptance.authenticated !== true) blockers.push('VENUE_ACCEPTANCE_NOT_AUTHENTICATED');
    if (acceptance.requestedQtyBtc !== input.requestedQtyBtc) blockers.push('VENUE_RECEIPT_REQUEST_QTY_MISMATCH');
    if (acceptance.acceptedQtyBtc > input.requestedQtyBtc) blockers.push('VENUE_ACCEPTED_QTY_EXCEEDS_REQUEST');
    if (acceptance.leverage !== input.leverage) blockers.push('VENUE_ACCEPTED_LEVERAGE_MISMATCH');
    if (acceptance.acceptedMarginUsd > 0.25 || acceptance.acceptedMarginUsd > input.requestedMarginUsd) {
      blockers.push('VENUE_ACCEPTED_MARGIN_EXCEEDS_CAP');
    }
    const computedNotional = acceptance.acceptedQtyBtc * acceptance.acceptedLimitPrice;
    const computedMargin = computedNotional / acceptance.leverage;
    if (Math.abs(computedNotional - acceptance.acceptedNotionalUsd) > 1e-8) blockers.push('VENUE_ACCEPTED_NOTIONAL_UNRECONCILED');
    if (Math.abs(computedMargin - acceptance.acceptedMarginUsd) > 1e-8) blockers.push('VENUE_ACCEPTED_MARGIN_UNRECONCILED');
    if (!acceptance.activeOrdersReconciled) blockers.push('ACTIVE_ORDERS_RECONCILIATION_MISSING');
    if (!acceptance.positionsReconciled) blockers.push('POSITIONS_RECONCILIATION_MISSING');
    if (!acceptance.executionsReconciled) blockers.push('EXECUTIONS_RECONCILIATION_MISSING');
  }
  return {
    schema: 'bitfinex_live_copy_sizing_readiness_v1' as const,
    status: blockers.length === 0 ? 'ACCEPTED_PROVEN' as const : 'UNKNOWN_NOT_PROVEN' as const,
    ready: blockers.length === 0,
    blockers,
    venueConstraints: constraints ?? null,
    acceptanceReceiptPresent: acceptance != null,
  };
}

/** Dashboard-safe preflight: missing live venue evidence is intentionally RED. */
export function missingBitfinexVenueEvidenceReadiness() {
  return assessBitfinexLiveCopySizingReadiness({
    requestedMarginUsd: 0.25,
    requestedQtyBtc: BITFINEX_BTC_PERP_EXPECTED_MIN_QTY,
    requestedLimitPrice: 62_500,
    leverage: 100,
    constraints: null,
    acceptance: null,
  });
}
