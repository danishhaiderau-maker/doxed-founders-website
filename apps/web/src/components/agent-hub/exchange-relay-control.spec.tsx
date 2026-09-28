import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ExchangeRelayControl } from './exchange-relay-control';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

test('active Bitfinex relay identifies the explicit two-lane roster', () => {
  const html = renderToStaticMarkup(
    React.createElement(ExchangeRelayControl, {
      slug: 'conservative-btc',
      signedIn: true,
      exchange: 'bitfinex',
      exchangeLabel: 'Bitfinex',
      exchangeConnected: true,
      relayState: 'active',
      showSelector: false,
      onStop: () => undefined,
    }),
  );

  assert.match(html, /Copying Continuous showcase orders on Bitfinex/);
  assert.equal(html.includes('Continuous + Type B'), false);
  assert.match(html, /explicitly live-eligible two-lane policy lifecycles/);
});

test('renders the durable relay transition without claiming an open position was closed', () => {
  const html = renderToStaticMarkup(
    React.createElement(ExchangeRelayControl, {
      slug: 'conservative-btc',
      signedIn: true,
      exchange: 'bitfinex',
      exchangeLabel: 'Bitfinex',
      exchangeConnected: true,
      relayState: 'paused',
      relayLastTransition: {
        at: '2026-08-14T00:00:00.000Z',
        actor: 'USER',
        action: 'STOPPED',
        reason: 'USER_REQUEST_STOP',
        cancelledPendingOrders: 2,
        openPositionsLeftOnExchange: true,
        relayEntryPolicy: 'NEXT_FRESH_ONLY',
      },
    }),
  );

  assert.match(html, /Latest relay transition/);
  assert.match(html, /STOPPED by user/);
  assert.match(html, /Pending copy orders cancelled: 2/);
  assert.match(html, /Open Bitfinex positions were left protected on the exchange/);
});
