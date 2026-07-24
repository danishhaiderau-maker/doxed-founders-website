import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  FOUNDER_SHORTCUT_SURFACES,
  shortcutEntries,
} from './founder-shortcuts-state';

const connectedState = {
  connected: true,
  workspaceName: 'Founder workspace',
  modeLabel: 'Hybrid',
};

describe('Founder shortcut rail', () => {
  it('keeps the intended navigation order below Founder Home', () => {
    assert.deepEqual(FOUNDER_SHORTCUT_SURFACES, [
      'agents',
      'ship',
      'node',
      'connections',
      'remote',
    ]);
  });

  it('gives every destination useful commands instead of placeholders', () => {
    for (const surface of FOUNDER_SHORTCUT_SURFACES) {
      const entries = shortcutEntries(surface, connectedState);
      assert.ok(entries.length >= 3, `${surface} needs at least three shortcuts`);
      assert.ok(entries.every((entry) => Boolean(entry.command)));
    }
  });

  it('changes Node and Remote calls to action when signed out', () => {
    const signedOut = { ...connectedState, connected: false };
    assert.match(shortcutEntries('node', signedOut)[0].label, /needs sign-in/i);
    assert.equal(shortcutEntries('remote', signedOut)[0].command, 'founderOs.signIn');
  });

  it('puts the daily evidence review in the Ship workflow', () => {
    const dailyReview = shortcutEntries('ship', connectedState).find(
      (entry) => entry.id === 'daily-quality-review',
    );
    assert.equal(dailyReview?.command, 'founderOs.runDailyQualityReview');
  });
});
