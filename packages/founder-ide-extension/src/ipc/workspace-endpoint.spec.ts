import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {
  createWorkspaceEndpointPresence,
  pipePathForEndpoint,
  pruneStaleWorkspaceEndpointPresences,
  removeWorkspaceEndpointPresence,
  workspaceEndpointDirectory,
  workspaceIdFor,
  writeWorkspaceEndpointPresence,
} from './workspace-endpoint.js';

const ENDPOINT_A = '11111111-2222-4333-8444-555555555555';
const ENDPOINT_B = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';

describe('workspace IPC endpoint presence', () => {
  it('gives separate windows separate pipe paths for the same install', () => {
    const a = pipePathForEndpoint('install-1', ENDPOINT_A);
    const b = pipePathForEndpoint('install-1', ENDPOINT_B);
    assert.notEqual(a, b);
    assert.match(a, /founder-ide-install-1-/);
  });

  it('derives a stable workspace id without storing a secret', () => {
    const workspace = path.join(os.tmpdir(), 'Founder Workspace');
    const presence = createWorkspaceEndpointPresence(
      { workspacePath: workspace, workspaceName: 'Founder Workspace' },
      ENDPOINT_A,
      new Date('2026-07-27T00:00:00.000Z'),
    );
    assert.equal(presence.workspaceId, workspaceIdFor(workspace));
    assert.equal(presence.workspaceName, 'Founder Workspace');
    assert.equal('ipcSecret' in presence, false);
    assert.equal('installId' in presence, false);
  });

  it('writes atomically, refreshes, and removes only its own record', () => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-endpoint-'));
    try {
      const presence = createWorkspaceEndpointPresence(
        { workspacePath: vault },
        ENDPOINT_A,
        new Date('2026-07-27T00:00:00.000Z'),
      );
      const refreshed = writeWorkspaceEndpointPresence(
        presence,
        vault,
        new Date('2026-07-27T00:00:15.000Z'),
      );
      const file = path.join(workspaceEndpointDirectory(vault), `${ENDPOINT_A}.json`);
      const stored = JSON.parse(fs.readFileSync(file, 'utf8')) as Record<string, unknown>;
      assert.equal(stored.heartbeatAt, refreshed.heartbeatAt);
      assert.equal(fs.readdirSync(workspaceEndpointDirectory(vault)).some((name) => name.endsWith('.tmp')), false);

      removeWorkspaceEndpointPresence(ENDPOINT_A, vault);
      assert.equal(fs.existsSync(file), false);
    } finally {
      fs.rmSync(vault, { recursive: true, force: true });
    }
  });

  it('prunes stale endpoint records without touching unrelated files', () => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-endpoint-'));
    try {
      const old = createWorkspaceEndpointPresence(
        { workspacePath: vault },
        ENDPOINT_A,
        new Date('2026-07-27T00:00:00.000Z'),
      );
      writeWorkspaceEndpointPresence(old, vault, new Date('2026-07-27T00:00:00.000Z'));
      const directory = workspaceEndpointDirectory(vault);
      fs.writeFileSync(path.join(directory, 'keep.txt'), 'keep', 'utf8');

      const removed = pruneStaleWorkspaceEndpointPresences(
        vault,
        Date.parse('2026-07-27T00:02:00.000Z'),
      );
      assert.equal(removed, 1);
      assert.equal(fs.existsSync(path.join(directory, 'keep.txt')), true);
    } finally {
      fs.rmSync(vault, { recursive: true, force: true });
    }
  });
});
