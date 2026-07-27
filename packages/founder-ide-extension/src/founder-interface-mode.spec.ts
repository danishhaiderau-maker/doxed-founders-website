import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  FOUNDER_INTERFACE_MODES,
  founderInterfaceModeDefinition,
  normalizeFounderInterfaceMode,
} from './founder-interface-mode';

describe('Founder interface modes', () => {
  it('defaults every unknown or missing value to Founder mode', () => {
    for (const value of [undefined, null, '', 'legacy', true]) {
      assert.equal(normalizeFounderInterfaceMode(value), 'founder');
    }
  });

  it('defines two explicit and complete interface profiles', () => {
    assert.deepEqual(FOUNDER_INTERFACE_MODES, [
      {
        id: 'founder',
        label: 'Founder mode',
        activityBarLocation: 'hidden',
        menuBarVisibility: 'hidden',
        commandCenter: false,
        layoutControl: false,
        statusBarVisible: false,
        editorTabs: 'single',
        advancedIdeTools: false,
      },
      {
        id: 'developer',
        label: 'Developer mode',
        activityBarLocation: 'default',
        menuBarVisibility: 'classic',
        commandCenter: true,
        layoutControl: true,
        statusBarVisible: true,
        editorTabs: 'multiple',
        advancedIdeTools: true,
      },
    ]);
    assert.equal(founderInterfaceModeDefinition('developer').label, 'Developer mode');
  });
});
