/**
 * HM #842 (24 Oct 2026): Bitfinex `v2/auth/r/ledgers/hist` rows are
 * [0 ID, 1 CURRENCY, 2 WALLET, 3 MTS, 4 null, 5 AMOUNT, 6 BALANCE, 7 null, 8 DESCRIPTION].
 * The readers used [4]/[7] (always null) so every amount read as 0. Fixtures below use the documented
 * 9-element layout, nulls at [4] and [7] included.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  BITFINEX_LEDGER_ROW,
  BitfinexLedgerUnverifiedError,
  BitfinexTradingClient,
  calculateBitfinexSessionPnl,
  parseBitfinexLedgerRow,
  resetBitfinexNonceLanesForTests,
} from './bitfinex-api.client';

const creds = { apiKey: 'ledger-layout-fixture', apiSecret: 'not-a-real-secret', testnet: false };

function ledgerRow(id: number, mts: number | null, amount: unknown, balance: unknown, description: unknown): unknown[] {
  return [id, 'USTF0', 'margin', mts, null, amount, balance, null, description];
}

const CLOSE = ledgerRow(9001, 1_700_000_020_000, 1.25, 501.25, 'Position closed @ 64390.0 (BTCF0:USTF0)');
const TRADING_FEE = ledgerRow(9002, 1_700_000_020_100, -0.2, 501.05, 'Trading fees for 0.01 BTCF0 @ 64390 on BFX (0.065%)');
const FUNDING_FEE = ledgerRow(9003, 1_700_000_030_000, -0.01, 501.04, 'Margin Funding Payment on wallet margin');

async function withLedger<T>(rows: unknown, run: (client: BitfinexTradingClient) => Promise<T>): Promise<T> {
  const originalFetch = globalThis.fetch;
  resetBitfinexNonceLanesForTests();
  globalThis.fetch = (async () => new Response(JSON.stringify(rows), { status: 200 })) as typeof fetch;
  try {
    return await run(new BitfinexTradingClient());
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test('ledger layout constants: AMOUNT at [5], DESCRIPTION at [8], MTS at [3], ID at [0]', () => {
  assert.equal(BITFINEX_LEDGER_ROW.AMOUNT, 5);
  assert.equal(BITFINEX_LEDGER_ROW.DESCRIPTION, 8);
  assert.equal(BITFINEX_LEDGER_ROW.MTS, 3);
  assert.equal(BITFINEX_LEDGER_ROW.ID, 0);
  assert.equal(CLOSE.length, 9);
  assert.equal(CLOSE[4], null);
  assert.equal(CLOSE[7], null);
});

test('parseBitfinexLedgerRow reads the documented 9-element row (nulls at [4] and [7])', () => {
  assert.deepEqual(parseBitfinexLedgerRow(CLOSE), {
    id: 9001, mts: 1_700_000_020_000, amount: 1.25, description: 'Position closed @ 64390.0 (BTCF0:USTF0)',
  });
});

test('parseBitfinexLedgerRow fails closed: null / non-finite AMOUNT or non-string DESCRIPTION is unverified, never 0', () => {
  for (const bad of [
    ledgerRow(1, 1, null, 0, 'Position closed'),
    ledgerRow(1, 1, undefined, 0, 'Position closed'),
    ledgerRow(1, 1, 'abc', 0, 'Position closed'),
    ledgerRow(1, 1, '', 0, 'Position closed'),
    ledgerRow(1, 1, Number.NaN, 0, 'Position closed'),
    ledgerRow(1, 1, Number.POSITIVE_INFINITY, 0, 'Position closed'),
    ledgerRow(1, 1, { v: 1 }, 0, 'Position closed'),
    ledgerRow(1, 1, 1.25, 0, null),
    ledgerRow(1, 1, 1.25, 0, 42),
    [1, 'USTF0', 'margin', 1, null, 1.25, 0, null], // legacy 8-element shape
    null,
    'row',
  ]) {
    assert.equal(parseBitfinexLedgerRow(bad), null, JSON.stringify(bad));
  }
});

test('getLedgerFeesSince sums AMOUNT[5] by DESCRIPTION[8] and is verified on clean rows', async () => {
  const out = await withLedger([CLOSE, TRADING_FEE, FUNDING_FEE], (c) => c.getLedgerFeesSince(creds, 0));
  assert.deepEqual(out, {
    tradingFeesUsd: 0.2, fundingFeesUsd: 0.01, realizedPnlUsd: 1.25, positionCloseRows: 1,
    verified: true, unverifiedRows: 0,
  });
});

test('getLedgerFeesSince marks a malformed row unverified instead of counting it as 0', async () => {
  const out = await withLedger(
    [CLOSE, ledgerRow(9004, 1_700_000_040_000, null, 0, 'Position closed'), ledgerRow(9005, 1, -0.3, 0, null)],
    (c) => c.getLedgerFeesSince(creds, 0),
  );
  assert.equal(out.verified, false);
  assert.equal(out.unverifiedRows, 2);
  assert.equal(out.positionCloseRows, 1, 'the null-AMOUNT close row is not counted as a 0 close');
});

test('getLedgerFeesSince is unverified (not zero-verified) when the read fails or is not an array', async () => {
  const notArray = await withLedger({ error: 'x' }, (c) => c.getLedgerFeesSince(creds, 0));
  assert.equal(notArray.verified, false);
  const originalFetch = globalThis.fetch;
  resetBitfinexNonceLanesForTests();
  globalThis.fetch = (async () => { throw new Error('network'); }) as typeof fetch;
  try {
    const failed = await new BitfinexTradingClient().getLedgerFeesSince(creds, 0);
    assert.equal(failed.verified, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('position-close ledger rows come from [0]/[3]/[5]/[8]; fees and funding are excluded', async () => {
  const rows = await withLedger([CLOSE, TRADING_FEE, FUNDING_FEE], (c) => c.getPositionCloseLedgerEntries(creds, 0));
  assert.equal(rows.length, 1);
  assert.equal(rows[0]?.ledgerId, '9001');
  assert.equal(rows[0]?.pnlUsd, 1.25);
  assert.equal(rows[0]?.closedAt.getTime(), 1_700_000_020_000);
  assert.equal(rows[0]?.description, 'Position closed @ 64390.0 (BTCF0:USTF0)');
  const realised = await withLedger([CLOSE, TRADING_FEE, FUNDING_FEE], (c) => c.getRealizedPnlSince(creds, 0));
  assert.equal(realised, 1.25);
});

test('a malformed ledger row makes the position-close read unverified (throws), never a 0 P&L', async () => {
  for (const bad of [
    ledgerRow(9006, 1_700_000_050_000, null, 0, 'Position closed'),
    ledgerRow(9007, 1_700_000_050_000, 0.5, 0, null),
    ledgerRow(9008, null, 0.5, 0, 'Position closed'),
  ]) {
    await assert.rejects(
      withLedger([CLOSE, bad], (c) => c.getRealizedPnlSince(creds, 0)),
      BitfinexLedgerUnverifiedError,
    );
    await assert.rejects(
      withLedger([CLOSE, bad], (c) => c.getPositionCloseLedgerEntries(creds, 0)),
      BitfinexLedgerUnverifiedError,
    );
  }
});

test('R7: session P&L is realized + unrealized; fees are never subtracted', () => {
  assert.equal(calculateBitfinexSessionPnl({ realizedPnlUsd: 1.25, unrealizedPnlUsd: -0.5 }), 0.75);
});
