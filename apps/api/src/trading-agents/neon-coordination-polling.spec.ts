import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import { TradingAgentsService } from './trading-agents.service';

test('coordination status uses a bounded JSON projection', async () => {
  let sqlText = '';
  const prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    $queryRaw: async (query: { strings?: readonly string[] }) => {
      sqlText = query.strings?.join('?') ?? String(query);
      return [{
        status: 'PAUSED', exchangeProvider: 'bitfinex', instanceMode: 'live',
        relayArmedAt: null, copyRelaySimActive: false, lastError: null,
      }];
    },
  };
  const service = new TradingAgentsService(
    prisma as never, {} as never, {} as never, {} as never,
    {} as never, {} as never, {} as never,
  );

  const result = await service.getCoordinationStatus('conservative-btc', 'user-1');
  assert.equal(result.projection, 'coordination_v1');
  assert.equal(result.instanceStatus, 'PAUSED');
  assert.equal(result.relayArmed, false);
  assert.match(sqlText, /\{copyRelaySim,active\}/);
  assert.doesNotMatch(sqlText, /SELECT\s+i\.\*|SELECT\s+\*/i);
  assert.doesNotMatch(sqlText, /SignalCycleParticipant|SignalCycleEvent/);
});

test('agent hub routine polling is hidden-aware, serial, and projection-only', () => {
  const page = readFileSync(
    resolve(__dirname, '../../../web/src/app/agent-hub/[slug]/page.client.tsx'),
    'utf8',
  );
  const effectStart = page.indexOf('let timer: ReturnType<typeof setTimeout>');
  const effectEnd = page.indexOf('async function toggleFollow', effectStart);
  const polling = page.slice(effectStart, effectEnd);
  assert.ok(effectStart > 0 && effectEnd > effectStart);
  assert.match(polling, /document\.hidden/);
  assert.match(polling, /inFlight = true/);
  assert.match(polling, /inFlight = false;\s*schedule\(pollMs\)/);
  assert.match(polling, /addEventListener\('visibilitychange'/);
  assert.doesNotMatch(polling, /setInterval/);
  assert.match(polling, /if \(liveViewEnabled\) await loadLive\(\)/);
  assert.match(polling, /else await loadCoordination\(\)/);
});
