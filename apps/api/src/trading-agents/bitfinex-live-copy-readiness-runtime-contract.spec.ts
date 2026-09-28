import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const subscriberSource = readFileSync(
  resolve(__dirname, 'signal-subscriber-execution.service.ts'),
  'utf8',
);
const agentServiceSource = readFileSync(
  resolve(__dirname, 'trading-agents.service.ts'),
  'utf8',
);

test('subscriber publishes fail-closed sizing readiness before and after an authenticated order receipt', () => {
  assert.match(subscriberSource, /bitfinexLiveCopySizingReadiness:\s*\n?\s*dash\.bitfinexLiveCopySizingReadiness \?\? missingBitfinexVenueEvidenceReadiness\(\)/);
  assert.match(subscriberSource, /getBtcPerpVenueConstraints\(\)/);
  assert.match(subscriberSource, /findOrder\(creds, orderId\)/);
  assert.match(subscriberSource, /getOpenPositionDetail\(creds\)/);
  assert.match(subscriberSource, /fetchOrderTrades\(creds, orderId\)/);
  assert.match(subscriberSource, /bitfinexLiveCopySizingReadiness: bitfinexSizingReadiness/);
  assert.match(subscriberSource, /bitfinex_sizing_readiness: bitfinexSizingReadiness/);
});

test('authenticated ops status exposes the same readiness object used by the dashboard', () => {
  assert.match(agentServiceSource, /bitfinexLiveCopySizingReadiness:\s*\n?\s*dash\.bitfinexLiveCopySizingReadiness \?\? missingBitfinexVenueEvidenceReadiness\(\)/);
});
