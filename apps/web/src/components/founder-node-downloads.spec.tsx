import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { FounderNodeDownloads } from './founder-node-downloads';

test('FounderNodeDownloads keeps its initial markup stable across server and browser environments', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');

  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: undefined,
  });
  const serverMarkup = renderToString(<FounderNodeDownloads />);

  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      platform: 'Win32',
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    },
  });
  const browserInitialMarkup = renderToString(<FounderNodeDownloads />);

  if (descriptor) {
    Object.defineProperty(globalThis, 'navigator', descriptor);
  } else {
    delete (globalThis as { navigator?: Navigator }).navigator;
  }

  assert.equal(browserInitialMarkup, serverMarkup);
});
