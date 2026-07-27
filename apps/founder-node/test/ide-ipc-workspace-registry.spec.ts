import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {
  IPC_PROTOCOL_VERSION,
  type IdeWorkspaceEndpointPresence,
  type IpcMessage,
} from 'founder-ide-extension/ipc';
import {
  IdeIpcWorkspaceRegistry,
  founderIdeSessionId,
  founderWorkspaceId,
  ideWorkspaceEndpointDirectory,
  readIdeWorkspaceEndpointPresences,
  type WorkspaceClient,
} from '../src/ide-ipc-workspace-registry.js';

const ENDPOINT_A = '11111111-2222-4333-8444-555555555555';
const ENDPOINT_B = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';

class FakeClient extends EventEmitter implements WorkspaceClient {
  active = false;
  sent: IpcMessage[] = [];

  async connect(): Promise<boolean> {
    this.active = true;
    this.emit('connected');
    return true;
  }

  disconnect(): void {
    const wasActive = this.active;
    this.active = false;
    if (wasActive) this.emit('disconnected', 'closed');
  }

  isHandshakeActive(): boolean {
    return this.active;
  }

  send(message: IpcMessage): boolean {
    if (!this.active) return false;
    this.sent.push(message);
    return true;
  }
}

function writePresence(
  vaultRoot: string,
  endpointId: string,
  workspacePath: string | null,
  heartbeatAt = '2026-07-27T00:00:00.000Z',
): void {
  const record: IdeWorkspaceEndpointPresence = {
    version: 1,
    endpointId,
    workspaceId: workspacePath ? founderWorkspaceId(workspacePath) : null,
    workspacePath,
    workspaceName: workspacePath ? path.basename(workspacePath) : 'Founder IDE',
    processId: 1234,
    protocolVersion: IPC_PROTOCOL_VERSION,
    startedAt: '2026-07-26T23:59:00.000Z',
    heartbeatAt,
  };
  const directory = ideWorkspaceEndpointDirectory(vaultRoot);
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(
    path.join(directory, `${endpointId}.json`),
    JSON.stringify(record),
    'utf8',
  );
}

describe('IdeIpcWorkspaceRegistry', () => {
  it('discovers and routes two workspace windows independently', async () => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-registry-'));
    const workspaceA = path.join(vault, 'alpha');
    const workspaceB = path.join(vault, 'beta');
    const clients = new Map<string, FakeClient>();
    try {
      writePresence(vault, ENDPOINT_A, workspaceA);
      writePresence(vault, ENDPOINT_B, workspaceB);
      const registry = new IdeIpcWorkspaceRegistry(vault, (endpointId) => {
        const client = new FakeClient();
        clients.set(endpointId, client);
        return client;
      });

      registry.refresh(Date.parse('2026-07-27T00:00:10.000Z'));
      await new Promise((resolve) => setTimeout(resolve, 0));

      const sessions = registry.discoverSessions();
      assert.equal(sessions.length, 2);
      assert.deepEqual(
        new Set(sessions.map((session) => session.folderPath)),
        new Set([path.resolve(workspaceA), path.resolve(workspaceB)]),
      );
      assert.equal(
        registry.resolveSession(founderIdeSessionId(ENDPOINT_A)),
        clients.get(ENDPOINT_A),
      );
      assert.equal(
        registry.resolveSession(founderIdeSessionId(ENDPOINT_B)),
        clients.get(ENDPOINT_B),
      );
      assert.notEqual(
        registry.resolveSession(founderIdeSessionId(ENDPOINT_A)),
        registry.resolveSession(founderIdeSessionId(ENDPOINT_B)),
      );
      const heartbeat: IpcMessage = {
        type: 'heartbeat',
        nonce: 'route-a',
        ts: '2026-07-27T00:00:10.000Z',
        at: '2026-07-27T00:00:10.000Z',
      };
      assert.equal(registry.sendToEndpoint(ENDPOINT_A, heartbeat), true);
      assert.equal(clients.get(ENDPOINT_A)?.sent.length, 1);
      assert.equal(clients.get(ENDPOINT_B)?.sent.length, 0);
      registry.disconnectAll();
    } finally {
      fs.rmSync(vault, { recursive: true, force: true });
    }
  });

  it('fails closed for unknown, malformed, and disconnected sessions', async () => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-registry-'));
    try {
      writePresence(vault, ENDPOINT_A, path.join(vault, 'alpha'));
      let client: FakeClient | null = null;
      const registry = new IdeIpcWorkspaceRegistry(vault, () => {
        client = new FakeClient();
        return client;
      });
      registry.refresh(Date.parse('2026-07-27T00:00:10.000Z'));
      await new Promise((resolve) => setTimeout(resolve, 0));

      assert.equal(registry.resolveSession('founder-ide:not-an-endpoint'), null);
      assert.equal(registry.resolveSession(founderIdeSessionId(ENDPOINT_B)), null);
      client!.active = false;
      assert.equal(registry.resolveSession(founderIdeSessionId(ENDPOINT_A)), null);
      registry.disconnectAll();
    } finally {
      fs.rmSync(vault, { recursive: true, force: true });
    }
  });

  it('removes stale endpoint records and never surfaces empty windows', async () => {
    const vault = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-registry-'));
    try {
      writePresence(vault, ENDPOINT_A, null, '2026-07-27T00:00:50.000Z');
      writePresence(vault, ENDPOINT_B, path.join(vault, 'stale'), '2026-07-27T00:00:00.000Z');
      const now = Date.parse('2026-07-27T00:01:30.001Z');
      const records = readIdeWorkspaceEndpointPresences(vault, now);
      assert.equal(records.length, 1);
      assert.equal(records[0]?.endpointId, ENDPOINT_A);
      assert.equal(
        fs.existsSync(
          path.join(ideWorkspaceEndpointDirectory(vault), `${ENDPOINT_B}.json`),
        ),
        false,
      );

      const registry = new IdeIpcWorkspaceRegistry(vault, () => new FakeClient());
      registry.refresh(now);
      await new Promise((resolve) => setTimeout(resolve, 0));
      assert.equal(registry.discoverSessions().length, 0);
      assert.equal(registry.hasActiveHandshake(), true);
      registry.disconnectAll();
    } finally {
      fs.rmSync(vault, { recursive: true, force: true });
    }
  });
});
