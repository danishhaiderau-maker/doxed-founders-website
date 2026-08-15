import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const executionPath = new URL(
  '../apps/api/src/trading-agents/signal-subscriber-execution.service.ts',
  import.meta.url,
);

test('direct executor wake acknowledges before exchange execution completes', async () => {
  const source = await readFile(executionPath, 'utf8');
  const method = source.slice(
    source.indexOf('async acceptDirectExecutorWake('),
    source.indexOf('private executorWakeKey(', source.indexOf('async acceptDirectExecutorWake(')),
  );

  assert.match(method, /this\.activeDirectWakes\.set\(laneKey, wake\)/);
  assert.match(method, /this\.executorWakeLaneKey\(wake\)/);
  assert.match(method, /void this\.executePersistedFastWake\(wake\)/);
  assert.match(method, /return true;/);
  assert.doesNotMatch(method, /await this\.executePersistedFastWake\(wake\)/);
});

test('durable fallback skips an identical successfully completed direct wake', async () => {
  const source = await readFile(executionPath, 'utf8');

  assert.match(source, /completedDirectWakeAt\.set\(this\.executorWakeKey\(wake\), Date\.now\(\)\)/);
  assert.match(source, /if \(this\.directWakeAlreadyCompleted\(wake\)\) return;/);
  assert.match(source, /const cutoff = Date\.now\(\) - 120_000;/);
});

test('signed LIMIT_UPDATED uses the private fast wake instead of the full poll cadence', async () => {
  const source = await readFile(executionPath, 'utf8');
  const fastWake = source.slice(
    source.indexOf('private async executePersistedFastWake('),
    source.indexOf('private async persistFastWakeTelemetry(', source.indexOf('private async executePersistedFastWake(')),
  );

  assert.match(fastWake, /wake\.trigger === 'LIMIT_UPDATED'/);
  assert.match(fastWake, /this\.tryImmediateSignedLimitUpdate\(/);
  assert.match(fastWake, /LIMIT_REPRICE_DISPATCHED/);
  assert.match(source, /status: SignalCycleStatus\.PENDING_ENTRY/);
  assert.match(source, /readFreshSignedShowcaseExactLimit\(/);
});

test('signed fast entry reuses same-invocation price and balance preflight', async () => {
  const source = await readFile(executionPath, 'utf8');

  assert.match(source, /this\.activeTrading\.getMarkPrice\(\)\.catch\(\(\) => null\)/);
  assert.match(source, /availableUsd,\s+markPrice,\s+exchangeBookProvenEmpty: flatPreflight,/);
  assert.match(source, /fastPreflight\?: \{[\s\S]*availableUsd: number;[\s\S]*markPrice: number;/);
  assert.match(source, /fastPreflight\?\.markPrice \?\? await this\.activeTrading\.getMarkPrice\(\)/);
  assert.match(source, /let available = fastPreflight\?\.availableUsd \?\? 0;/);
});

test('entry and reprice persist complete stage timing around the exchange action', async () => {
  const source = await readFile(executionPath, 'utf8');
  for (const stage of [
    'queueEnteredAtMs',
    'executorStartedAtMs',
    'databasePreflightStartedAtMs',
    'databasePreflightCompletedAtMs',
    'bitfinexRequestStartedAtMs',
    'exchangeAckAtMs',
    'persistenceStartedAtMs',
    'persistenceCompletedAtMs',
  ]) assert.match(source, new RegExp(stage));
  assert.match(source, /'EXECUTION_TIMING'/);
  assert.match(source, /schema: 'relay_execution_timing_v1'/);
});

test('mirror diff followed by stale-no-exposure is immutable excluded evidence', async () => {
  const source = await readFile(executionPath, 'utf8');
  assert.match(source, /eventType: 'MIRROR_DIFF'/);
  assert.match(source, /'NEGATIVE_EVIDENCE'/);
  assert.match(source, /event: 'MIRROR_DIFF_STALE_NO_EXPOSURE'/);
  assert.match(source, /analysis_exclusion_reasons: \['MIRROR_DIFF_STALE_NO_EXPOSURE'\]/);
});

test('profit-lock stop replacement preserves continuous exact-quantity protection', async () => {
  const source = await readFile(executionPath, 'utf8');
  const start = source.indexOf('// Submit-new-before-cancel-old.');
  const end = source.indexOf('// Option A', start);
  assert.ok(start >= 0 && end > start, 'profit-lock replacement block must exist');
  const replacement = source.slice(start, end);

  const submitAt = replacement.indexOf('submitStopOrder');
  const persistAt = replacement.indexOf('PROFIT_LOCK_STOP_REPLACEMENT_ACKNOWLEDGED');
  const cancelOldAt = replacement.indexOf('Profit-lock supersede old stop');
  assert.ok(submitAt >= 0 && persistAt > submitAt && cancelOldAt > persistAt);
  assert.match(replacement, /qty: exchangeProtectedQty/);
  assert.match(source, /const exchangeProtectedQty = Math\.abs\(position\.amount\)/);
  assert.doesNotMatch(replacement.slice(0, submitAt), /cancelManagedOrderGone/);
});

test('real-side hard-stop fallback cannot widen beyond the canonical policy', async () => {
  const source = await readFile(executionPath, 'utf8');
  const start = source.indexOf('function realSideSafetyNetHardStopMarginPct(');
  const end = source.indexOf('/** Exact-copy entries', start);
  assert.ok(start >= 0 && end > start, 'hard-stop fallback function must exist');
  const hardStop = source.slice(start, end);
  assert.match(hardStop, /return -13;/);
  assert.doesNotMatch(hardStop, /return -18;/);
});
