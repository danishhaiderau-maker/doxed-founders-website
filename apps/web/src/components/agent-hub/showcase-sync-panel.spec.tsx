import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  BREACH_CHECKS_REQUIRED,
  BREACH_MIN_DURATION_MS,
  LIVE_DEFAULT_STOP_THRESHOLD_PCT,
  LIVE_MIN_STOP_THRESHOLD_PCT,
  ShowcaseSyncPanel,
} from './showcase-sync-panel';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

test('live safety is a fixed sustained 60 percent server guard', () => {
  assert.equal(LIVE_DEFAULT_STOP_THRESHOLD_PCT, 60);
  assert.equal(LIVE_MIN_STOP_THRESHOLD_PCT, 60);
  assert.equal(BREACH_CHECKS_REQUIRED, 3);
  assert.equal(BREACH_MIN_DURATION_MS, 90_000);

  const html = renderToStaticMarkup(
    React.createElement(ShowcaseSyncPanel, {
      input: { botConnected: true },
      mode: 'live',
      liveActive: true,
    }),
  );
  assert.match(html, /Automatic live safety guard: fixed at 60%/);
  assert.match(html, /3 low observations/);
  assert.match(html, /at least 90s/);
  assert.match(html, /runs on the server even when this page is closed/);
  assert.doesNotMatch(html, /Stop live copy if sync drops below/);
  assert.doesNotMatch(html, /Also close all open positions/);
  assert.doesNotMatch(html, /<select/);
});
