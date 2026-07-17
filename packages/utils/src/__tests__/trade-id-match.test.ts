import assert from 'node:assert/strict';
import test from 'node:test';
import { isMirrorableLaneTradeId } from '../trade-id-match';

test('only Continuous and Type B are mirrorable', () => {
  assert.equal(isMirrorableLaneTradeId('cont-deadbeef1234'), true);
  assert.equal(isMirrorableLaneTradeId('tbhv1-deadbeef1234'), true);
  assert.equal(isMirrorableLaneTradeId('scan-deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('srmv2s-deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('a160v2-deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('unknown-deadbeef1234'), false);
});
