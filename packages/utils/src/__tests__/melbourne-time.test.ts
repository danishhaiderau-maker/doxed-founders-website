import assert from 'node:assert/strict';
import test from 'node:test';
import { formatMelbourneDateTime } from '../melbourne-time';

test('formats Melbourne midnight as 00 rather than 24', () => {
  assert.equal(
    formatMelbourneDateTime('2026-08-01T14:57:21.000Z'),
    '2026-08-02 00:57:21 AEST',
  );
});
