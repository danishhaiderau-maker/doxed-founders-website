import assert from 'node:assert/strict';
import test from 'node:test';
import {
  chaseSelectionLabel,
  directionGap,
  directionGapLabel,
} from './agent-direction-gap';

test('raw AI gap 30 maps to execution bucket 3', () => {
  assert.deepEqual(directionGap(30), { raw: 30, bucket: 3, bucketLabel: '3' });
  assert.equal(directionGapLabel(30), 'Raw AI gap 30/100 · execution bucket 3');
});

test('execution UI caps high gap labels as bucket 5+', () => {
  assert.deepEqual(directionGap(65), { raw: 65, bucket: 6, bucketLabel: '5+' });
});

test('missing gaps and chase selections are never fabricated', () => {
  assert.equal(directionGap(undefined), null);
  assert.equal(directionGapLabel(undefined), 'Gap not recorded');
  assert.equal(chaseSelectionLabel(undefined), 'not reported');
  assert.equal(chaseSelectionLabel([4, 3, 4]), '3, 4');
});

test('the terminal chase selection is labelled 5+ instead of exact chase 5', () => {
  assert.equal(chaseSelectionLabel([3, 4, 5]), '3, 4, 5+');
});
