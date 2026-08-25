import assert from 'node:assert/strict';
import test from 'node:test';
import { isMirrorableLaneTradeId } from '../trade-id-match';

test('only explicitly relay-approved showcase lanes are mirrorable', () => {
  assert.equal(isMirrorableLaneTradeId('cont-deadbeef1234'), true);
  assert.equal(isMirrorableLaneTradeId('o29atr-deadbeef1234'), true);
  assert.equal(isMirrorableLaneTradeId('retired-deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('unknown-deadbeef1234'), false);
});
