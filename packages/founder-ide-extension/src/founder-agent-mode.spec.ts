import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { afterEach, describe, it } from 'node:test';
import {
  founderAgentModeDefinition,
  normalizeFounderAgentMode,
  readFounderAgentMode,
  writeFounderAgentMode,
} from './founder-agent-mode';

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe('Founder agent mode', () => {
  it('defaults unknown values to one editing owner', () => {
    assert.equal(normalizeFounderAgentMode(undefined), 'focus');
    assert.equal(normalizeFounderAgentMode('swarm'), 'focus');
    assert.match(founderAgentModeDefinition('team').summary, /read-only advisers/i);
  });

  it('persists the native and extension preference without deleting other keys', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-agent-mode-'));
    roots.push(root);
    const file = path.join(root, 'preferences.json');
    fs.writeFileSync(file, JSON.stringify({ companion: true }), 'utf8');
    writeFounderAgentMode('team', file);
    assert.equal(readFounderAgentMode(file), 'team');
    assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).companion, true);
  });
});
