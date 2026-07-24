import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildWorkspaceContextIndex,
  dependencyImpact,
  extractImportSpecifiers,
  formatWorkspaceContextForPrompt,
  parseDecisionLedger,
  parseWorkspaceContextIndex,
  rankWorkspaceContextFiles,
  symbolCandidateScore,
  workspaceCacheContext,
  workspaceContextFileNeedsRefresh,
  type WorkspaceContextFile,
} from './workspace-context-state';

function file(path: string, symbols: string[] = []): WorkspaceContextFile {
  return {
    path,
    languageId: path.split('.').at(-1) ?? 'text',
    size: 10,
    mtimeMs: 100,
    sha256: 'a'.repeat(64),
    symbols,
    imports: [],
  };
}

describe('Founder workspace context index', () => {
  it('loads only the matching workspace and schema version', () => {
    const index = buildWorkspaceContextIndex('workspace-1', [file('src/app.ts')]);
    assert.equal(parseWorkspaceContextIndex(index, 'workspace-1')?.files.length, 1);
    assert.equal(parseWorkspaceContextIndex(index, 'workspace-2'), null);
    assert.equal(parseWorkspaceContextIndex({ ...index, version: 1 }, 'workspace-1'), null);
  });

  it('invalidates only when file size or modification time changes', () => {
    const previous = file('src/app.ts');
    assert.equal(workspaceContextFileNeedsRefresh(previous, previous), false);
    assert.equal(
      workspaceContextFileNeedsRefresh(previous, { ...previous, mtimeMs: 101 }),
      true,
    );
    assert.equal(
      workspaceContextFileNeedsRefresh(previous, { ...previous, size: 11 }),
      true,
    );
  });

  it('ranks matching paths and symbols ahead of generic files', () => {
    const index = buildWorkspaceContextIndex('workspace-1', [
      file('README.md'),
      file('src/auth/session.ts', ['rotateSessionToken']),
      file('src/billing/usage.ts', ['reserveCost']),
    ]);
    assert.equal(
      rankWorkspaceContextFiles(index, 'fix the session token rotation')[0]?.path,
      'src/auth/session.ts',
    );
  });

  it('matches camel-case symbols even when the path does not contain the query', () => {
    const index = buildWorkspaceContextIndex('workspace-1', [
      file('src/a.ts', ['rotateSessionToken']),
      file('src/b.ts', ['renderDashboard']),
    ]);
    assert.equal(
      rankWorkspaceContextFiles(index, 'rotate the session token')[0]?.path,
      'src/a.ts',
    );
  });

  it('formats a compact project map without source contents', () => {
    const index = buildWorkspaceContextIndex('workspace-1', [
      file('src/auth/session.ts', ['rotateSessionToken']),
    ], '2026-07-22T00:00:00.000Z');
    const prompt = formatWorkspaceContextForPrompt(index, 'session token');
    assert.match(prompt, /Founder local project map/);
    assert.match(prompt, /src\/auth\/session\.ts/);
    assert.match(prompt, /rotateSessionToken/);
    assert.doesNotMatch(prompt, /secret source contents/);
  });

  it('prioritizes shallow source entry points over deep test files for symbol analysis', () => {
    assert.ok(
      symbolCandidateScore('apps/api/src/main.ts')
      > symbolCandidateScore('apps/api/src/features/auth/session.spec.ts'),
    );
    assert.ok(
      symbolCandidateScore('packages/extension/src/extension.ts')
      > symbolCandidateScore('scripts/archive/legacy/helper.ts'),
    );
  });

  it('extracts stable imports without storing source contents', () => {
    assert.deepEqual(
      extractImportSpecifiers(
        "import { api } from './api';\nconst auth = require('../auth');\nexport * from './types';",
        'ts',
      ),
      ['../auth', './api', './types'],
    );
  });

  it('surfaces reverse dependency impact before shared files are edited', () => {
    const api = { ...file('src/api.ts', ['request']), imports: [] };
    const page = { ...file('src/page.tsx', ['Page']), imports: ['./api'] };
    const test = { ...file('src/api.spec.ts', ['testApi']), imports: ['./api'] };
    const index = buildWorkspaceContextIndex('workspace-1', [api, page, test]);
    assert.deepEqual(dependencyImpact(index, 'src/api.ts').importedBy, [
      'src/api.spec.ts',
      'src/page.tsx',
    ]);
    assert.match(formatWorkspaceContextForPrompt(index, 'change api request'), /used by src\/api\.spec\.ts, src\/page\.tsx/);
  });

  it('remembers rejected approaches with source provenance', () => {
    const decisions = parseDecisionLedger(
      '# Decisions\n\n## [rejected] Store API keys in project files\nSecrets must remain in encrypted user storage.\n\n## Better routing\nStatus: accepted\nUse Flash by default.',
      '.github/founder-os/decisions.md',
      'b'.repeat(64),
    );
    assert.equal(decisions.length, 2);
    assert.equal(decisions[0]?.status, 'rejected');
    assert.equal(decisions[0]?.sourceHash, 'b'.repeat(64));
    const index = buildWorkspaceContextIndex('workspace-1', [file('src/keys.ts')], undefined, decisions);
    const prompt = formatWorkspaceContextForPrompt(index, 'store api keys');
    assert.match(prompt, /REJECTED: Store API keys in project files/);
    assert.match(prompt, /unless the founder explicitly reopens/);
  });

  it('invalidates reusable answers when a relevant file hash changes', () => {
    const first = buildWorkspaceContextIndex('workspace-1', [
      { ...file('src/auth/session.ts', ['rotateSessionToken']), sha256: 'a'.repeat(64) },
      { ...file('src/unrelated.ts', ['unrelated']), sha256: 'b'.repeat(64) },
    ]);
    const changedRelevant = buildWorkspaceContextIndex('workspace-1', [
      { ...file('src/auth/session.ts', ['rotateSessionToken']), sha256: 'c'.repeat(64) },
      { ...file('src/unrelated.ts', ['unrelated']), sha256: 'b'.repeat(64) },
    ]);
    const changedUnrelated = buildWorkspaceContextIndex('workspace-1', [
      { ...file('src/auth/session.ts', ['rotateSessionToken']), sha256: 'a'.repeat(64) },
      { ...file('src/unrelated.ts', ['unrelated']), sha256: 'd'.repeat(64) },
    ]);
    const original = workspaceCacheContext(first, 'explain session token rotation');
    assert.notEqual(
      original?.contextHash,
      workspaceCacheContext(changedRelevant, 'explain session token rotation')?.contextHash,
    );
    assert.equal(
      original?.contextHash,
      workspaceCacheContext(changedUnrelated, 'explain session token rotation')?.contextHash,
    );
  });
});
