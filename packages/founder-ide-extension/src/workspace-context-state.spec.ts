import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildWorkspaceContextIndex,
  formatWorkspaceContextForPrompt,
  parseWorkspaceContextIndex,
  rankWorkspaceContextFiles,
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
  };
}

describe('Founder workspace context index', () => {
  it('loads only the matching workspace and schema version', () => {
    const index = buildWorkspaceContextIndex('workspace-1', [file('src/app.ts')]);
    assert.equal(parseWorkspaceContextIndex(index, 'workspace-1')?.files.length, 1);
    assert.equal(parseWorkspaceContextIndex(index, 'workspace-2'), null);
    assert.equal(parseWorkspaceContextIndex({ ...index, version: 2 }, 'workspace-1'), null);
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
});
