import assert from 'node:assert/strict';
import path from 'node:path';
import { describe, it } from 'node:test';
import type { ChildProcess } from 'node:child_process';

import {
  embeddedRelayExecutable,
  launchEmbeddedRelay,
} from './embedded-relay';

describe('embedded Founder relay', () => {
  it('resolves the relay inside the IDE resources directory', () => {
    const appRoot = path.join('C:', 'Founder IDE', 'resources', 'app');
    assert.equal(
      embeddedRelayExecutable(appRoot, 'win32'),
      path.join('C:', 'Founder IDE', 'resources', 'founder-relay', 'Founder Node.exe'),
    );
  });

  it('uses the running IDE executable as the Windows install root', () => {
    assert.equal(
      embeddedRelayExecutable(
        'C:\\unexpected\\app-root',
        'win32',
        'C:\\Users\\founder\\Programs\\Founder IDE\\Founder IDE.exe',
      ),
      'C:\\Users\\founder\\Programs\\Founder IDE\\resources\\founder-relay\\Founder Node.exe',
    );
  });

  it('does not launch when the current IDE build has no bundled relay', async () => {
    const result = await launchEmbeddedRelay('C:\\Founder IDE\\resources\\app', 'win32', {
      existsSync: () => false,
    });
    assert.equal(result.state, 'not-bundled');
  });

  it('starts the bundled relay hidden in embedded mode', async () => {
    let captured:
      | {
          executable: string;
          args: readonly string[];
          options: Parameters<typeof import('node:child_process').spawn>[2];
        }
      | undefined;
    const fakeChild = {
      pid: 4242,
      unref() {},
    } as ChildProcess;

    const result = await launchEmbeddedRelay('C:\\Founder IDE\\resources\\app', 'win32', {
      environment: { ELECTRON_RUN_AS_NODE: '1' },
      existsSync: () => true,
      homedir: () => 'C:\\Users\\founder',
      isProcessAlive: () => false,
      processExecutablePath: () => null,
      spawnProcess: (executable, args, options) => {
        captured = { executable, args, options };
        return fakeChild;
      },
    });

    assert.equal(result.state, 'started');
    assert.equal(result.pid, 4242);
    assert.deepEqual(captured?.args, ['--embedded-founder-ide']);
    assert.equal(captured?.options?.windowsHide, true);
    assert.equal(captured?.options?.env?.FOUNDER_NODE_EMBEDDED, '1');
    assert.equal(captured?.options?.env?.ELECTRON_RUN_AS_NODE, undefined);
  });

  it('replaces a legacy standalone lock instead of treating it as embedded', async () => {
    let spawned = false;
    const fakeChild = {
      pid: 4243,
      unref() {},
    } as ChildProcess;

    const result = await launchEmbeddedRelay('C:\\Founder IDE\\resources\\app', 'win32', {
      existsSync: () => true,
      homedir: () => 'C:\\Users\\founder',
      isProcessAlive: () => true,
      processExecutablePath: () =>
        'C:\\Users\\founder\\AppData\\Local\\Programs\\Founder Node\\Founder Node.exe',
      readLockFile: () =>
        JSON.stringify({
          pid: 99,
          exePath: 'C:\\Users\\founder\\AppData\\Local\\Programs\\Founder Node\\Founder Node.exe',
        }),
      spawnProcess: () => {
        spawned = true;
        return fakeChild;
      },
    });

    assert.equal(result.state, 'started');
    assert.equal(spawned, true);
  });

  it('replaces an embedded lock when Windows reused the pid for another process', async () => {
    let spawned = false;
    const executable = 'C:\\Founder IDE\\resources\\founder-relay\\Founder Node.exe';
    const fakeChild = { pid: 4244, unref() {} } as ChildProcess;

    const result = await launchEmbeddedRelay('C:\\Founder IDE\\resources\\app', 'win32', {
      existsSync: () => true,
      homedir: () => 'C:\\Users\\founder',
      isProcessAlive: () => true,
      processExecutablePath: () => 'C:\\Windows\\System32\\unrelated.exe',
      readLockFile: () => JSON.stringify({ pid: 99, exePath: executable }),
      spawnProcess: () => {
        spawned = true;
        return fakeChild;
      },
    });

    assert.equal(result.state, 'started');
    assert.equal(spawned, true);
  });

  it('keeps the running embedded relay when pid and executable both match', async () => {
    const executable = 'C:\\Founder IDE\\resources\\founder-relay\\Founder Node.exe';
    const result = await launchEmbeddedRelay('C:\\Founder IDE\\resources\\app', 'win32', {
      existsSync: () => true,
      homedir: () => 'C:\\Users\\founder',
      isProcessAlive: () => true,
      processExecutablePath: () => executable,
      readLockFile: () => JSON.stringify({ pid: 99, exePath: executable }),
    });

    assert.equal(result.state, 'already-running');
    assert.equal(result.pid, 99);
  });
});
