import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  coordinationPrompt,
  findAgentRisks,
  intentSimilarity,
  parsePresence,
  type FounderAgentPresence,
} from './agent-coordination-state';

const now = new Date('2026-07-21T10:00:00.000Z');
const presence = (overrides: Partial<FounderAgentPresence>): FounderAgentPresence => ({
  version: 1,
  id: 'self',
  workspacePath: 'C:\\workspace',
  workspaceName: 'workspace',
  branch: 'feature/a',
  title: 'Redesign Founder settings navigation',
  provider: 'founder-os-code',
  status: 'working',
  ownedFiles: ['src/settings.tsx'],
  startedAt: now.toISOString(),
  heartbeatAt: now.toISOString(),
  ...overrides,
});

describe('Founder agent coordination', () => {
  it('detects shared-file work in the same workspace', () => {
    const self = presence({});
    const peer = presence({ id: 'peer', title: 'Fix settings form', ownedFiles: ['src/settings.tsx'] });
    const risks = findAgentRisks(self, [peer], now.getTime());
    assert.equal(risks.length, 1);
    assert.deepEqual(risks[0]?.overlappingFiles, ['src/settings.tsx']);
  });

  it('leaves unrelated work and separate workspaces alone', () => {
    const self = presence({});
    const unrelated = presence({ id: 'docs', title: 'Write release notes', ownedFiles: ['docs/release.md'] });
    const separate = presence({ id: 'other', workspacePath: 'C:\\another', ownedFiles: ['src/settings.tsx'] });
    assert.deepEqual(findAgentRisks(self, [unrelated, separate], now.getTime()), []);
  });

  it('detects meaningfully similar intent without a claimed file', () => {
    assert.ok(intentSimilarity('redesign founder settings navigation', 'fix founder settings navigation') >= 0.34);
    const self = presence({ ownedFiles: [] });
    const peer = presence({ id: 'peer', title: 'Fix Founder settings navigation', ownedFiles: [] });
    assert.equal(findAgentRisks(self, [peer], now.getTime()).length, 1);
  });

  it('builds a model-agnostic coordination block', () => {
    const self = presence({});
    const peer = presence({ id: 'peer', title: 'Fix settings form' });
    const prompt = coordinationPrompt(self, [peer], now.getTime());
    assert.match(prompt, /Live agent coordination/);
    assert.match(prompt, /COORDINATE/);
    assert.doesNotMatch(prompt, /DeepSeek|GLM|OpenAI/);
  });

  it('rejects malformed lease data', () => {
    assert.equal(parsePresence(null), null);
    assert.equal(parsePresence({ version: 1 }), null);
    assert.equal(parsePresence(presence({} ))?.id, 'self');
  });
});
