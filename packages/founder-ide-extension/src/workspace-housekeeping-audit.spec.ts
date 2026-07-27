import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import { afterEach, describe, it } from 'node:test';
import { auditWorkspaceHousekeeping } from './workspace-housekeeping-audit';

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe('workspace housekeeping audit', () => {
  it('proposes exact cache paths without treating source as disposable', async () => {
    const root = await createWorkspace();
    await fs.mkdir(path.join(root, 'src'), { recursive: true });
    await fs.writeFile(path.join(root, 'src', 'index.ts'), 'export const keep = true;');
    await fs.mkdir(path.join(root, 'node_modules', 'pkg'), { recursive: true });
    await fs.writeFile(path.join(root, 'node_modules', 'pkg', 'index.js'), 'cache');
    await fs.mkdir(path.join(root, 'coverage'), { recursive: true });
    await fs.writeFile(path.join(root, 'coverage', 'report.json'), '{}');
    await fs.mkdir(path.join(root, 'dist'), { recursive: true });
    await fs.writeFile(path.join(root, 'dist', 'release.exe'), 'release');

    const audit = await auditWorkspaceHousekeeping(root);

    assert.deepEqual(
      audit.candidates.map((candidate) => candidate.path),
      ['coverage', 'dist', 'node_modules'],
    );
    assert.equal(
      audit.candidates.find((candidate) => candidate.path === 'node_modules')
        ?.recommendedAction,
      'delete',
    );
    assert.equal(
      audit.candidates.find((candidate) => candidate.path === 'dist')
        ?.recommendedAction,
      'archive',
    );
    assert.equal(
      audit.candidates.some((candidate) => candidate.path.startsWith('src')),
      false,
    );
  });

  it('fails closed when the measurement boundary is reached', async () => {
    const root = await createWorkspace();
    await fs.mkdir(path.join(root, 'node_modules', 'pkg'), { recursive: true });
    for (let index = 0; index < 12; index += 1) {
      await fs.writeFile(
        path.join(root, 'node_modules', 'pkg', `${index}.js`),
        'generated',
      );
    }

    const audit = await auditWorkspaceHousekeeping(root, { maxEntries: 3 });

    assert.equal(audit.truncated, true);
    assert.equal(audit.candidates[0]?.recommendedAction, 'keep');
    assert.equal(audit.candidates[0]?.reversible, false);
  });

  it('never enters source-control metadata', async () => {
    const root = await createWorkspace();
    await fs.mkdir(path.join(root, '.git', 'node_modules'), { recursive: true });
    await fs.writeFile(path.join(root, '.git', 'node_modules', 'object'), 'keep');

    const audit = await auditWorkspaceHousekeeping(root);

    assert.deepEqual(audit.candidates, []);
  });

  it('identifies archives, suspicious old copies, and exact duplicates as review-only', async () => {
    const root = await createWorkspace();
    const duplicate = Buffer.alloc(1_200_000, 7);
    await fs.writeFile(path.join(root, 'release.zip'), 'archive');
    await fs.writeFile(path.join(root, 'settings.ts.bak'), 'old copy');
    await fs.writeFile(path.join(root, 'copy-a.bin'), duplicate);
    await fs.writeFile(path.join(root, 'copy-b.bin'), duplicate);

    const audit = await auditWorkspaceHousekeeping(root);
    const archive = audit.candidates.find(
      (candidate) => candidate.category === 'archive',
    );
    const obsolete = audit.candidates.find(
      (candidate) => candidate.category === 'obsolete_source',
    );
    const duplicates = audit.candidates.filter(
      (candidate) => candidate.category === 'duplicate',
    );

    assert.equal(archive?.recommendedAction, 'keep');
    assert.equal(obsolete?.recommendedAction, 'keep');
    assert.equal(duplicates.length, 2);
    assert.equal(duplicates.every((candidate) =>
      candidate.recommendedAction === 'keep'), true);
    assert.deepEqual(duplicates[0]?.referencedBy, ['copy-b.bin']);
  });
});

async function createWorkspace() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'founder-housekeeping-'));
  roots.push(root);
  return root;
}
