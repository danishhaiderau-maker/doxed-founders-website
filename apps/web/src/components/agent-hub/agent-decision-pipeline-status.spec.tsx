import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { AgentDecisionPipelineStatus } from './agent-decision-pipeline-status';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

test('pending approval is clearly virtual and uses the same raw gap/bucket scale', () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentDecisionPipelineStatus, {
      dashboard: {
        pendingApproval: {
          tradeId: 'cont-gap-30',
          status: 'APPROVE_PENDING',
          direction: 'LONG',
          reason: 'Waiting for selected chase bucket 3',
          rawScoreGap: 30,
          gapBucket: 3,
          chaseCount: 2,
          selectedChaseBuckets: [3, 4],
          exactLimitPrice: null,
        },
      },
    } as never),
  );

  assert.match(html, /Approved candidate · waiting in virtual chase/);
  assert.match(html, /Raw AI gap 30\/100 · execution bucket 3/);
  assert.match(html, /Virtual chase now: 2/);
  assert.match(html, /Entry buckets selected: 3, 4/);
  assert.match(html, /not yet a resting Bitfinex order/);
});

test('missing direction-only scores are labelled unavailable, never as zero confidence', () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentDecisionPipelineStatus, {
      dashboard: {},
    } as never),
  );
  assert.match(html, /Probability confidence is not requested/);
  assert.equal(html.includes('0%'), false);
});
