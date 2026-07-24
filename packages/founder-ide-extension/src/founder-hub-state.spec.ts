import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  FOUNDER_WORKSPACE_MODES,
  normalizeWorkspaceMode,
  workspaceModeDefinition,
} from './founder-hub-state';

describe('Founder hub workspace modes', () => {
  it('keeps the public choice to Local, Hybrid, and Founder Cloud', () => {
    assert.deepEqual(
      FOUNDER_WORKSPACE_MODES.map((mode) => mode.id),
      ['local', 'hybrid', 'cloud'],
    );
  });

  it('defaults missing or legacy values to Hybrid', () => {
    assert.equal(normalizeWorkspaceMode(undefined), 'hybrid');
    assert.equal(normalizeWorkspaceMode('public'), 'hybrid');
  });

  it('returns the matching user-facing definition', () => {
    assert.equal(workspaceModeDefinition('local').label, 'Local');
    assert.match(workspaceModeDefinition('cloud').services, /remote agents/i);
  });
});
