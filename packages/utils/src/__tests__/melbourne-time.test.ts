import assert from 'node:assert/strict';
import test from 'node:test';
import { formatMelbourneDateTime } from '../melbourne-time';

test('formats Melbourne midnight as 00 rather than 24', () => {
  assert.equal(
    formatMelbourneDateTime('2026-08-01T14:57:21.000Z'),
    '2026-08-02 00:57:21 AEST',
  );
});

test('collapses a pre-formatted Melbourne hour 24 to 00 next day (bot mapper regression)', () => {
  // The bot mapper occasionally emits `24:57:21 AEST` around Melbourne midnight.
  // The formatter must round-trip it to the next day at 00:57, not return it
  // verbatim with an out-of-range hour.
  assert.equal(
    formatMelbourneDateTime('2026-08-02 24:57:21 AEST'),
    '2026-08-03 00:57:21 AEST',
  );
});

test('passes through a well-formed pre-formatted Melbourne string unchanged', () => {
  // Healthy bot output must not be mutated — this guards against the
  // re-normalization accidentally corrupting already-correct strings.
  // (Seconds are always emitted in the output even if the input omitted them.)
  assert.equal(
    formatMelbourneDateTime('2026-08-03 04:50:07 AEST'),
    '2026-08-03 04:50:07 AEST',
  );
  // No-second input is normalized to include seconds; this is intentional and
  // matches the format emitted for ISO inputs.
  assert.equal(
    formatMelbourneDateTime('2026-08-03 04:50 AEST'),
    '2026-08-03 04:50:00 AEST',
  );
});
