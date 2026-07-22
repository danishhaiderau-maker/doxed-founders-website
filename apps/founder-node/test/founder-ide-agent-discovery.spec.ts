import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { describe, it } from 'node:test';
import {
  discoverFounderIdeAgentSessions,
  discoverFounderIdeAgentWorkspaces,
  discoverFounderIdeAgents,
  readFounderIdePresences,
  __testHooks,
} from '../src/founder-ide-agent-discovery';

function writePresence(root: string, id: string, heartbeatAt: string): void {
  fs.writeFileSync(path.join(root, `${id}.json`), JSON.stringify({
    version: 1,
    id,
    workspacePath: 'C:\\repo',
    workspaceName: 'Founder repo',
    branch: 'feature/agents',
    title: `Task ${id}`,
    provider: 'founder-os-code',
    status: 'working',
    ownedFiles: ['src/app.ts'],
    startedAt: heartbeatAt,
    heartbeatAt,
  }));
}

describe('Founder IDE agent discovery', () => {
  it('surfaces fresh task leases and ignores stale ones', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-agents-'));
    const now = Date.now();
    writePresence(root, 'fresh', new Date(now).toISOString());
    writePresence(root, 'stale', new Date(now - __testHooks.PRESENCE_TTL_MS - 1).toISOString());
    try {
      assert.deepEqual(readFounderIdePresences(root, now).map((item) => item.id), ['fresh']);
      assert.equal(discoverFounderIdeAgents(root)[0]?.status, 'running');
      assert.equal(discoverFounderIdeAgentSessions(root)[0]?.ideProvider, 'founder-ide');
      assert.equal(discoverFounderIdeAgentWorkspaces(root)[0]?.hasActiveAgent, true);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
