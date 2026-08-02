import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ExchangeRelayControl } from './exchange-relay-control';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

test('active Bitfinex relay identifies Continuous as the only live lane', () => {
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
  assert.match(html, /Type B remains paper\/research-only/);
});
