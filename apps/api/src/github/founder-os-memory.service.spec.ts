import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { FounderOsMemoryService } from './founder-os-memory.service';

describe('FounderOsMemoryService', () => {
  it('produces stable files and lets unchanged polls become no-op syncs', async () => {
    const updatedAt = new Date('2026-07-28T02:00:00.000Z');
    const goal = {
      id: 'founder-v1',
      version: 2,
      objective: 'Ship Founder OS V1.',
      constraints: ['No unapproved deletion'],
      successEvidence: [
        {
          id: 'installed',
          label: 'Installed QA passes',
          kind: 'visual',
          required: true,
        },
      ],
      status: 'active',
      updatedAt: updatedAt.toISOString(),
    };
    const prisma = {
      founder: {
        findUnique: async () => ({
          id: 'founder-1',
          name: 'Danish',
          updatedAt,
          projects: [
            {
              name: 'Founder OS',
              updatedAt,
              roadmapItems: [
                {
                  title: 'Founder IDE V1',
                  status: 'IN_PROGRESS',
                  updatedAt,
                },
              ],
            },
          ],
        }),
      },
      founderBuilderSettings: {
        findUnique: async () => ({
          currentGoalFocus: 'Fallback goal',
          updatedAt,
          memoryGraph: {
            _founderGoalControl: {
              schemaVersion: 1,
              workspaces: {
                project: {
                  goal,
                  decisions: [],
                  resolutions: [],
                  updatedAt: updatedAt.toISOString(),
                },
              },
            },
          },
        }),
      },
      buildQueueItem: {
        findMany: async () => [
          {
            id: 'task-1',
            title: 'Run installed QA',
            status: 'CAPTURED',
            kind: 'TASK',
            updatedAt,
          },
        ],
      },
      founderEvent: {
        findFirst: async () => ({
          createdAt: new Date('2026-07-28T01:30:00.000Z'),
        }),
      },
    };
    const writes: Array<Array<{ path: string; content: string }>> = [];
    const github = {
      resolveRepo: async () => 'founder/founder-os',
      hasToken: async () => true,
      listCommits: async () => [{ message: 'feat: ship stable memory' }],
      getRepoFile: async (
        _userId: string,
        _repo: string,
        path: string,
      ) => path.endsWith('decisions.md') || path.endsWith('launch-checklist.md')
        ? 'existing'
        : null,
      upsertRepoFilesBatch: async (
        _userId: string,
        _repo: string,
        files: Array<{ path: string; content: string }>,
      ) => {
        writes.push(structuredClone(files));
        return writes.length === 1
          ? { updated: files.length, skipped: 0 }
          : { updated: 0, skipped: files.length };
      },
    };
    const service = new FounderOsMemoryService(
      prisma as never,
      github as never,
    );

    const first = await service.syncProjectMemoryToRepo('user-1');
    const second = await service.syncProjectMemoryToRepo('user-1');

    assert.deepEqual(first, {
      synced: true,
      repo: 'founder/founder-os',
    });
    assert.deepEqual(second, {
      synced: true,
      repo: 'founder/founder-os',
      unchanged: true,
    });
    assert.equal(writes.length, 2);
    assert.deepEqual(writes[0], writes[1]);
    const files = new Map(writes[0].map((file) => [file.path, file.content]));
    assert.match(files.get('.github/founder-os/goal.json') ?? '', /Ship Founder OS V1/);
    assert.match(
      files.get('.github/founder-os/tasks.json') ?? '',
      /2026-07-28T02:00:00.000Z/,
    );
    assert.doesNotMatch(
      files.get('.github/founder-os/project-context.md') ?? '',
      /\bago\b/,
    );
  });
});
