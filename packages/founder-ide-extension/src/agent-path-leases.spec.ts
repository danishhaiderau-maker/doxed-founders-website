import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { afterEach, describe, it } from 'node:test';
import { FounderPathLeaseStore } from './agent-path-leases';

const roots: string[] = [];

function store(ttlMs = 180_000): FounderPathLeaseStore {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-path-lease-'));
  roots.push(root);
  return new FounderPathLeaseStore(root, ttlMs);
}

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe('Founder path leases', () => {
  it('atomically grants one owner and denies a fresh competing owner', () => {
    const leases = store();
    const first = leases.claim('C:\\work', 'src/app.ts', 'task-a', 1_000);
    const second = leases.claim('C:\\work', 'src/app.ts', 'task-b', 1_001);
    assert.equal(first.ok, true);
    assert.equal(second.ok, false);
    if (!second.ok) assert.equal(second.ownerTaskId, 'task-a');
  });

  it('refreshes the same owner without changing its fencing token', () => {
    const leases = store();
    const first = leases.claim('C:\\work', 'src/app.ts', 'task-a', 1_000);
    const second = leases.claim('C:\\work', 'src/app.ts', 'task-a', 2_000);
    assert.equal(first.ok && second.ok, true);
    if (first.ok && second.ok) assert.equal(second.lease.fencingToken, first.lease.fencingToken);
  });

  it('takes over an expired claim and rejects the stale fencing token', () => {
    const leases = store(100);
    const first = leases.claim('C:\\work', 'src/app.ts', 'task-a', 1_000);
    const second = leases.claim('C:\\work', 'src/app.ts', 'task-b', 1_101);
    assert.equal(first.ok && second.ok, true);
    if (first.ok && second.ok) {
      assert.notEqual(second.lease.fencingToken, first.lease.fencingToken);
      assert.equal(leases.validate(first.lease, 1_102).ok, false);
      assert.equal(leases.validate(second.lease, 1_102).ok, true);
    }
  });

  it('isolates identical relative paths in separate workspaces', () => {
    const leases = store();
    assert.equal(leases.claim('C:\\one', 'src/app.ts', 'task-a', 1_000).ok, true);
    assert.equal(leases.claim('C:\\two', 'src/app.ts', 'task-b', 1_000).ok, true);
  });

  it('releases all paths owned by a completed task', () => {
    const leases = store();
    leases.claim('C:\\work', 'src/app.ts', 'task-a', 1_000);
    leases.claim('C:\\work', 'src/other.ts', 'task-a', 1_000);
    assert.equal(leases.claimsForTask('task-a', 1_001).length, 2);
    leases.releaseTask('task-a');
    assert.equal(leases.claimsForTask('task-a', 1_001).length, 0);
  });
});
