import assert from 'node:assert/strict';
import test from 'node:test';

import { ideProviderLabel } from './ide-provider-label';

test('formats known Founder IDE provider aliases', () => {
  assert.equal(ideProviderLabel('founder-ide'), 'Founder IDE');
  assert.equal(ideProviderLabel('founder_ide'), 'Founder IDE');
  assert.equal(ideProviderLabel('Founder IDE'), 'Founder IDE');
});

test('formats supported external provider aliases', () => {
  assert.equal(ideProviderLabel('cursor'), 'Cursor');
  assert.equal(ideProviderLabel('claude_code'), 'Claude Code');
  assert.equal(ideProviderLabel('vs-code'), 'VS Code');
});

test('uses a truthful generic label for absent or unknown providers', () => {
  assert.equal(ideProviderLabel(null), 'your IDE');
  assert.equal(ideProviderLabel('future-provider'), 'your IDE');
});
