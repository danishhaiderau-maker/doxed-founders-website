import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { dislocEntryOrderMode } from './signal-subscriber-execution.service';

test('LIVE-FIX-1510: DISLOC entries default to IOC at market', () => {
  assert.equal(dislocEntryOrderMode('dsl-1791842085-86', {}), 'IOC_MARKET');
  assert.equal(dislocEntryOrderMode('dsl-1', { DISLOC_ENTRY_ORDER_MODE: 'ioc_cap' }), 'IOC_CAP');
  assert.equal(dislocEntryOrderMode('dsl-1', { DISLOC_ENTRY_ORDER_MODE: 'LIMIT' }), 'LIMIT');
  assert.equal(dislocEntryOrderMode('dsl-1', { DISLOC_ENTRY_ORDER_MODE: 'bogus' }), 'IOC_MARKET');
});

test('LIVE-FIX-1510: non-DISLOC trades keep the legacy limit path', () => {
  assert.equal(dislocEntryOrderMode('gs1-ab2302016ee3', {}), null);
  assert.equal(dislocEntryOrderMode(undefined, {}), null);
});
