import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { claimStartupOwnership } from '../src/startup-ownership';

describe('Founder Node startup ownership', () => {
  it('does not touch the global owner when Electron already has an owner', () => {
    let globalCalls = 0;
    const result = claimStartupOwnership({
      requestElectronLock: () => false,
      releaseElectronLock: () => {
        assert.fail('A lock this process never acquired cannot be released');
      },
      acquireGlobalLock: () => {
        globalCalls += 1;
        return true;
      },
    });

    assert.equal(result, 'electron-owner-active');
    assert.equal(globalCalls, 0);
  });

  it('claims the cross-path owner only after Electron ownership', () => {
    const events: string[] = [];
    const result = claimStartupOwnership({
      requestElectronLock: () => {
        events.push('electron');
        return true;
      },
      releaseElectronLock: () => events.push('release'),
      acquireGlobalLock: () => {
        events.push('global');
        return true;
      },
    });

    assert.equal(result, 'acquired');
    assert.deepEqual(events, ['electron', 'global']);
  });

  it('releases Electron ownership when a legacy global owner remains active', () => {
    const events: string[] = [];
    const result = claimStartupOwnership({
      requestElectronLock: () => {
        events.push('electron');
        return true;
      },
      releaseElectronLock: () => events.push('release'),
      acquireGlobalLock: () => {
        events.push('global');
        return false;
      },
    });

    assert.equal(result, 'global-owner-active');
    assert.deepEqual(events, ['electron', 'global', 'release']);
  });
});
