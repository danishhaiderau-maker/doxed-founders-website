import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  getDeploymentRuntimeStatus,
  probeDeploymentRuntimeStatus,
} from '../src/deployment-mode-status.js';

describe('deployment runtime status', () => {
  it('returns a bounded best-effort status without blocking the event loop', async () => {
    const vaultRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-status-test-'));
    const startedAt = Date.now();
    let timerFired = false;
    const timer = new Promise<void>((resolve) => {
      setTimeout(() => {
        timerFired = true;
        resolve();
      }, 25);
    });

    try {
      const statusPromise = probeDeploymentRuntimeStatus(vaultRoot);
      await timer;
      assert.equal(timerFired, true);

      const status = await statusPromise;
      assert.ok(Date.now() - startedAt < 8_000);
      assert.match(status.probedAt, /^\d{4}-\d{2}-\d{2}T/);
      assert.ok(['online', 'offline', 'not-installed'].includes(status.forgejo));
      assert.equal(typeof status.tunnel.active, 'boolean');
      assert.equal(typeof status.tailscale.reachable, 'boolean');
      assert.equal(status.sqlite.file, null);
      assert.equal(status.sqlite.sizeBytes, null);
    } finally {
      fs.rmSync(vaultRoot, { recursive: true, force: true });
    }
  });

  it('returns the last-known status immediately while a refresh runs', () => {
    const vaultRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'founder-status-cache-test-'));
    const startedAt = Date.now();

    try {
      const status = getDeploymentRuntimeStatus(vaultRoot);
      assert.ok(Date.now() - startedAt < 100);
      assert.equal(typeof status.tunnel.active, 'boolean');
      assert.equal(typeof status.tailscale.reachable, 'boolean');
    } finally {
      fs.rmSync(vaultRoot, { recursive: true, force: true });
    }
  });
});
