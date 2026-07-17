/**
 * Unit tests for FounderIdeAdapter (Phase 3, Task 5).
 *
 * Pins the fail-closed guarantee:
 *   - isConnected() is false when no installId is resolvable
 *   - isConnected() is false when FounderNodeService reports no handshake
 *   - isConnected() is true when the node reports active handshake
 *   - readWorkspace() returns [] when disconnected
 *   - applyEdits() returns 'ipc_not_connected' per edit when disconnected
 *   - runCommand() returns exitCode 126 when disconnected
 *   - applyEdits() / runCommand() surface 'ipc_dispatch_not_wired' when
 *     connected (the HTTPS-to-IPC relay is a follow-up)
 *
 * Uses a stub FounderNodeService so the tests run offline (no Prisma).
 *
 * Run with:
 *   npx tsx --test apps/api/src/execution-manager/adapters/founder-ide.adapter.spec.ts
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { FounderIdeAdapter } from './founder-ide.adapter.js';

// ---------------------------------------------------------------------------
// Stub FounderNodeService — only the slice the adapter touches.
// ---------------------------------------------------------------------------

interface HandshakeEntry {
  active: boolean;
  updatedAt: number;
}

class StubFounderNodeService {
  /** nodeId → handshake state. */
  readonly handshakes = new Map<string, HandshakeEntry>();
  /** installId → nodeId lookup. */
  readonly installMap = new Map<string, string>();
  /** Throws when set (lets us test "lookup failed" paths). */
  findNodeError: Error | null = null;

  setHandshake(nodeId: string, active: boolean, now = Date.now()): void {
    this.handshakes.set(nodeId, { active, updatedAt: now });
  }

  // Mirrors the real FounderNodeService method names the adapter calls.
  isIdeHandshakeActive(nodeId: string, now = Date.now()): boolean {
    const entry = this.handshakes.get(nodeId);
    if (!entry) return false;
    if (now - entry.updatedAt > 30_000) return false;
    return entry.active;
  }

  async findNodeByInstallId(installId: string): Promise<{ nodeId: string } | null> {
    if (this.findNodeError) throw this.findNodeError;
    return this.installMap.has(installId) ? { nodeId: this.installMap.get(installId)! } : null;
  }
}

/** Poll a predicate until it returns true or the timeout elapses. */
async function waitFor(fn: () => boolean, timeoutMs = 500, stepMs = 5): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (fn()) return;
    await new Promise((r) => setTimeout(r, stepMs));
  }
}

describe('FounderIdeAdapter (Phase 3 fail-closed IPC dispatch)', () => {
  let originalInstallId: string | undefined;

  beforeEach(() => {
    originalInstallId = process.env.FOUNDER_IDE_INSTALL_ID;
  });
  beforeEach(() => {
    delete process.env.FOUNDER_IDE_INSTALL_ID;
  });

  // Restore after the suite finishes (node:test has no afterEach hook here,
  // so we restore inside each test's teardown-by-convention at suite end).
  it('cleanup hook: restore env', () => {
    if (originalInstallId !== undefined) process.env.FOUNDER_IDE_INSTALL_ID = originalInstallId;
  });

  describe('isConnected()', () => {
    it('returns false when FOUNDER_IDE_INSTALL_ID is unset', () => {
      const nodes = new StubFounderNodeService();
      const adapter = new FounderIdeAdapter(nodes as never);
      assert.equal(adapter.isConnected(), false);
    });

    it('returns false when no node is registered for the install', async () => {
      process.env.FOUNDER_IDE_INSTALL_ID = 'install-abc';
      const nodes = new StubFounderNodeService();
      const adapter = new FounderIdeAdapter(nodes as never);
      // First call kicks off an async lookup; wait a tick for it to resolve.
      assert.equal(adapter.isConnected(), false);
      await new Promise((r) => setTimeout(r, 5));
      // Still false because no entry was found.
      assert.equal(adapter.isConnected(), false);
    });

    it('returns false when node has no handshake entry', async () => {
      process.env.FOUNDER_IDE_INSTALL_ID = 'install-abc';
      const nodes = new StubFounderNodeService();
      nodes.installMap.set('install-abc', 'node-1');
      const adapter = new FounderIdeAdapter(nodes as never);
      // First call triggers the async nodeId lookup; second call sees the
      // cached value but no handshake entry → still false.
      assert.equal(adapter.isConnected(), false);
      await new Promise((r) => setTimeout(r, 5));
      assert.equal(adapter.isConnected(), false);
    });

    it('returns false when node handshake is active=false', async () => {
      process.env.FOUNDER_IDE_INSTALL_ID = 'install-abc';
      const nodes = new StubFounderNodeService();
      nodes.installMap.set('install-abc', 'node-1');
      nodes.setHandshake('node-1', false);
      const adapter = new FounderIdeAdapter(nodes as never);
      assert.equal(adapter.isConnected(), false);
      await new Promise((r) => setTimeout(r, 5));
      assert.equal(adapter.isConnected(), false);
    });

    it('returns true when node handshake is active=true', async () => {
      process.env.FOUNDER_IDE_INSTALL_ID = 'install-abc';
      const nodes = new StubFounderNodeService();
      nodes.installMap.set('install-abc', 'node-1');
      nodes.setHandshake('node-1', true);
      const adapter = new FounderIdeAdapter(nodes as never);
      // First call triggers async lookup; returns false this time.
      assert.equal(adapter.isConnected(), false);
      // Wait for the async installMap lookup to resolve + cache.
      await waitFor(() => adapter.isConnected(), 500);
      // Now the cached nodeId is present + handshake active → true.
      assert.equal(adapter.isConnected(), true);
    });

    it('returns false when FounderNodeService is not wired (no DI)', () => {
      process.env.FOUNDER_IDE_INSTALL_ID = 'install-abc';
      const adapter = new FounderIdeAdapter(undefined);
      assert.equal(adapter.isConnected(), false);
    });
  });

  describe('readWorkspace() fail-closed', () => {
    it('returns [] when not connected', async () => {
      const nodes = new StubFounderNodeService();
      const adapter = new FounderIdeAdapter(nodes as never);
      const result = await adapter.readWorkspace('/some/path');
      assert.deepEqual(result, []);
    });

    it('returns [] when connected (relay not yet wired)', async () => {
      process.env.FOUNDER_IDE_INSTALL_ID = 'install-abc';
      const nodes = new StubFounderNodeService();
      nodes.installMap.set('install-abc', 'node-1');
      nodes.setHandshake('node-1', true);
      const adapter = new FounderIdeAdapter(nodes as never);
      await waitFor(() => adapter.isConnected(), 200);
      assert.equal(adapter.isConnected(), true);
      const result = await adapter.readWorkspace();
      assert.deepEqual(result, []);
    });
  });

  describe('applyEdits() fail-closed', () => {
    it('returns ipc_not_connected per edit when disconnected', async () => {
      const nodes = new StubFounderNodeService();
      const adapter = new FounderIdeAdapter(nodes as never);
      const result = await adapter.applyEdits([
        { path: '/a.txt', kind: 'overwrite', content: 'x' },
        { path: '/b.txt', kind: 'create', content: 'y' },
      ]);
      assert.equal(result.length, 2);
      assert.equal(result[0].ok, false);
      assert.equal(result[0].error, 'ipc_not_connected');
      assert.equal(result[1].ok, false);
      assert.equal(result[1].error, 'ipc_not_connected');
    });

    it('returns ipc_dispatch_not_wired per edit when connected', async () => {
      process.env.FOUNDER_IDE_INSTALL_ID = 'install-abc';
      const nodes = new StubFounderNodeService();
      nodes.installMap.set('install-abc', 'node-1');
      nodes.setHandshake('node-1', true);
      const adapter = new FounderIdeAdapter(nodes as never);
      await waitFor(() => adapter.isConnected(), 500);
      const result = await adapter.applyEdits([
        { path: '/a.txt', kind: 'overwrite', content: 'x' },
      ]);
      assert.equal(result.length, 1);
      assert.equal(result[0].ok, false);
      assert.equal(result[0].error, 'ipc_dispatch_not_wired');
    });
  });

  describe('runCommand() fail-closed', () => {
    it('returns exitCode 126 + ipc_not_connected when disconnected', async () => {
      const nodes = new StubFounderNodeService();
      const adapter = new FounderIdeAdapter(nodes as never);
      const result = await adapter.runCommand('npm test');
      assert.equal(result.exitCode, 126);
      assert.equal(result.stderr, 'ipc_not_connected');
      assert.equal(result.stdout, '');
    });

    it('returns exitCode 126 + ipc_dispatch_not_wired when connected', async () => {
      process.env.FOUNDER_IDE_INSTALL_ID = 'install-abc';
      const nodes = new StubFounderNodeService();
      nodes.installMap.set('install-abc', 'node-1');
      nodes.setHandshake('node-1', true);
      const adapter = new FounderIdeAdapter(nodes as never);
      await waitFor(() => adapter.isConnected(), 500);
      const result = await adapter.runCommand('npm test');
      assert.equal(result.exitCode, 126);
      assert.equal(result.stderr, 'ipc_dispatch_not_wired');
    });
  });

  describe('connect() / disconnect()', () => {
    it('connect() resolves without throwing whether or not the service is wired', async () => {
      const adapterWith = new FounderIdeAdapter(new StubFounderNodeService() as never);
      const adapterWithout = new FounderIdeAdapter(undefined);
      await adapterWith.connect();
      await adapterWithout.connect();
      // No assertion needed — resolving without throwing is the contract.
      assert.ok(true);
    });

    it('disconnect() resolves without throwing', async () => {
      const adapter = new FounderIdeAdapter(new StubFounderNodeService() as never);
      await adapter.disconnect();
      assert.ok(true);
    });
  });
});
