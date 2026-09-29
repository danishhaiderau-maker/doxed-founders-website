import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { isMirrorableLaneTradeId } from '../trade-id-match';
import {
  ACTIVE_TILE_ID_PREFIXES,
  RELAY_ELIGIBLE_TILE_ID_PREFIXES,
  RETIRED_TILE_LANES,
} from '../generated/tile-registry.generated';

test('only registry relay-eligible tile prefixes are mirrorable', () => {
  for (const prefix of ACTIVE_TILE_ID_PREFIXES) {
    assert.equal(
      isMirrorableLaneTradeId(`${prefix}-deadbeef1234`),
      RELAY_ELIGIBLE_TILE_ID_PREFIXES.includes(prefix),
    );
  }
  assert.equal(isMirrorableLaneTradeId('retired-deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('unknown-deadbeef1234'), false);
});

test('Continuous and retired o29atr identifiers are never mirrorable', () => {
  assert.equal(isMirrorableLaneTradeId('cont-deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('o29atr-deadbeef1234'), false);
  assert.ok(!RELAY_ELIGIBLE_TILE_ID_PREFIXES.includes('cont'));
  assert.ok(!RELAY_ELIGIBLE_TILE_ID_PREFIXES.includes('o29atr'));
});

test('trade-id-match holds no hand-maintained prefix list', () => {
  const source = readFileSync(join(__dirname, '..', 'trade-id-match.ts'), 'utf8');
  assert.match(source, /new Set\(RELAY_ELIGIBLE_TILE_ID_PREFIXES\)/);
  assert.doesNotMatch(source, /'cont'|'o29atr'/);
  for (const lane of RETIRED_TILE_LANES) assert.ok(!source.includes(lane));
});
