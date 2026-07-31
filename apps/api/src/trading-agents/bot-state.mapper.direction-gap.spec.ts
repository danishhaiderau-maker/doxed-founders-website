import assert from 'node:assert/strict';
import test from 'node:test';
import { mapBotStateToDashboard, type BotApiState } from './bot-state.mapper';

test('direction-only score gap and virtual chase approval survive the API mapping', () => {
  const bot: BotApiState = {
    price: 64_000,
    strategy_mode: 'RESEARCH',
    last_ai: {
      decision: 'APPROVE',
      direction: 'LONG',
      confidence_requested: false,
      long_score: 65,
      short_score: 35,
    },
    last_approve_outcome: {
      trade_id: 'scan-gap-30',
      status: 'PENDING',
      direction: 'LONG',
      reason: 'WAITING_DASHBOARD_CHASE',
      ts: '2026-07-31T00:00:00.000Z',
    },
    chase_execution_buckets: {
      '2_chases': false,
      '3_chases': true,
      '4_chases': true,
      '5+_chases': false,
    },
    trades_map: {
      'scan-gap-30': {
        ai: { long_score: 65, short_score: 35 },
        signal_ref: {
          trade_id: 'scan-gap-30',
          dir: 'LONG',
          status: 'AWAITING_DASHBOARD_CHASE',
          score_gap: 30,
          dashboard_virtual_chase_count: 2,
          planned_limit_price: 63_936,
          entry_limit_policy: 'STRUCTURAL_SR_V1',
        },
      },
    },
    signal_info: {
      active: true,
      count: 1,
      signals: [
        {
          trade_id: 'scan-gap-30',
          dir: 'LONG',
          status: 'AWAITING_DASHBOARD_CHASE',
          score_gap: 30,
          dashboard_virtual_chase_count: 2,
          planned_limit_price: 63_936,
          entry_limit_policy: 'STRUCTURAL_SR_V1',
        },
      ],
    },
  };

  const dashboard = mapBotStateToDashboard(bot);

  assert.deepEqual(dashboard.latestAiVerdict, {
    decision: 'APPROVE',
    direction: 'LONG',
    longScore: 65,
    shortScore: 35,
    rawScoreGap: 30,
    gapBucket: 3,
    winProbability: 0,
    reason: 'Monitoring',
    comment: '',
    blockReason: null,
    edgeScore: 0,
    requiredEdge: 3,
    marketRegime: 'UNKNOWN',
    updatedAt: null,
  });
  assert.deepEqual(dashboard.pendingApproval, {
    tradeId: 'scan-gap-30',
    status: 'PENDING',
    direction: 'LONG',
    reason: 'WAITING_DASHBOARD_CHASE',
    rawScoreGap: 30,
    gapBucket: 3,
    chaseCount: 2,
    selectedChaseBuckets: [3, 4],
    exactLimitPrice: 63_936,
    entryLimitPolicy: 'STRUCTURAL_SR_V1',
    updatedAt: '2026-07-31T00:00:00.000Z',
  });
  assert.equal(dashboard.liveBook.activeSignals[0]?.rawScoreGap, 30);
  assert.equal(dashboard.liveBook.activeSignals[0]?.gapBucket, 3);
  assert.equal(dashboard.liveBook.activeSignals[0]?.chaseCount, 2);
  assert.deepEqual(dashboard.liveBook.activeSignals[0]?.selectedChaseBuckets, [3, 4]);
  assert.match(dashboard.aiReasoning, /Raw LONG\/SHORT gap: 30\/100 · execution bucket 3/);
  assert.doesNotMatch(dashboard.aiReasoning, /Win probability: 0%/);
});
