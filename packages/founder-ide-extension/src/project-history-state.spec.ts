import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  parseFounderProjectHistory,
  recordFounderProject,
  renameFounderProject,
  setFounderProjectArchived,
  setFounderProjectPinned,
  visibleFounderProjects,
  type FounderProjectRecord,
} from './project-history-state';

function record(
  id: string,
  lastOpenedAt: string,
  overrides: Partial<FounderProjectRecord> = {},
): FounderProjectRecord {
  return {
    id,
    name: id,
    nameSource: 'automatic',
    uri: `C:\\Projects\\${id}`,
    kind: 'folder',
    lastOpenedAt,
    pinnedAt: null,
    archivedAt: null,
    ...overrides,
  };
}

describe('Founder project history', () => {
  it('records one project per identity while preserving a founder name and pin', () => {
    const existing = [
      record('alpha', '2026-07-20T00:00:00.000Z', {
        name: 'Launch workspace',
        nameSource: 'custom',
        pinnedAt: '2026-07-21T00:00:00.000Z',
        archivedAt: '2026-07-22T00:00:00.000Z',
      }),
    ];
    const result = recordFounderProject(existing, {
      id: 'alpha',
      name: 'alpha',
      uri: 'C:\\Projects\\alpha',
      kind: 'folder',
      openedAt: '2026-07-26T00:00:00.000Z',
    });
    assert.equal(result.length, 1);
    assert.equal(result[0].name, 'Launch workspace');
    assert.equal(result[0].pinnedAt, '2026-07-21T00:00:00.000Z');
    assert.equal(result[0].archivedAt, null);
    assert.equal(result[0].lastOpenedAt, '2026-07-26T00:00:00.000Z');
  });

  it('sorts pinned projects first and hides archived projects by default', () => {
    let projects = [
      record('recent', '2026-07-26T00:00:00.000Z'),
      record('pinned', '2026-07-20T00:00:00.000Z'),
      record('archived', '2026-07-25T00:00:00.000Z'),
    ];
    projects = setFounderProjectPinned(
      projects,
      'pinned',
      true,
      '2026-07-26T01:00:00.000Z',
    );
    projects = setFounderProjectArchived(
      projects,
      'archived',
      true,
      '2026-07-26T02:00:00.000Z',
    );
    assert.deepEqual(
      visibleFounderProjects(projects, false).map((project) => project.id),
      ['pinned', 'recent'],
    );
    assert.deepEqual(
      visibleFounderProjects(projects, true).map((project) => project.id),
      ['archived'],
    );
  });

  it('renames, archives, restores, and sanitizes persisted state', () => {
    let projects = [record('alpha', '2026-07-26T00:00:00.000Z')];
    projects = renameFounderProject(projects, 'alpha', '  Founder   launch  ');
    projects = setFounderProjectArchived(
      projects,
      'alpha',
      true,
      '2026-07-26T02:00:00.000Z',
    );
    projects = setFounderProjectArchived(
      projects,
      'alpha',
      false,
      '2026-07-26T03:00:00.000Z',
    );
    assert.equal(projects[0].name, 'Founder launch');
    assert.equal(projects[0].nameSource, 'custom');
    assert.equal(projects[0].archivedAt, null);

    assert.deepEqual(
      parseFounderProjectHistory([
        projects[0],
        projects[0],
        { id: 'broken', name: '', uri: '', lastOpenedAt: 'not-a-date' },
      ]),
      projects,
    );
  });
});
