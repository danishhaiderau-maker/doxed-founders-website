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

  assert.match(method, /setImmediate\(\(\) => \{/);
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

test('signed fast entry reuses same-invocation price and balance preflight', async () => {
  const source = await readFile(executionPath, 'utf8');

  assert.match(source, /this\.activeTrading\.getMarkPrice\(\)\.catch\(\(\) => null\)/);
  assert.match(source, /\{ availableUsd, markPrice \},/);
  assert.match(source, /fastPreflight\?: \{ availableUsd: number; markPrice: number \}/);
  assert.match(source, /fastPreflight\?\.markPrice \?\? await this\.activeTrading\.getMarkPrice\(\)/);
  assert.match(source, /let available = fastPreflight\?\.availableUsd \?\? 0;/);
});
