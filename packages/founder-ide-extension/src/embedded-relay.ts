import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export type EmbeddedRelayPlatform = 'win32' | 'darwin' | 'linux';

export interface EmbeddedRelayLaunchResult {
  state: 'started' | 'already-running' | 'not-bundled' | 'unsupported';
  executable: string | null;
  pid?: number;
}

interface RelayLock {
  pid?: number;
  exePath?: string;
}

interface EmbeddedRelayDeps {
  environment?: NodeJS.ProcessEnv;
  existsSync?: typeof fs.existsSync;
  homedir?: () => string;
  isProcessAlive?: (pid: number) => boolean;
  processExecutablePath?: (
    pid: number,
    platform: NodeJS.Platform,
  ) => string | null;
  readLockFile?: (file: string) => string;
  runtimeExecutable?: string;
  spawnProcess?: (
    executable: string,
    args: readonly string[],
    options: Parameters<typeof spawn>[2],
  ) => ChildProcess;
}

export function embeddedRelayExecutable(
  appRoot: string,
  platform: NodeJS.Platform = process.platform,
  runtimeExecutable?: string,
): string | null {
  const resourcesRoot = path.dirname(appRoot);
  switch (platform as EmbeddedRelayPlatform) {
    case 'win32':
      if (runtimeExecutable) {
        return path.join(
          path.dirname(runtimeExecutable),
          'resources',
          'founder-relay',
          'Founder Node.exe',
        );
      }
      return path.join(resourcesRoot, 'founder-relay', 'Founder Node.exe');
    case 'darwin':
      return path.join(
        resourcesRoot,
        'founder-relay',
        'Founder Node.app',
        'Contents',
        'MacOS',
        'Founder Node',
      );
    case 'linux':
      return path.join(resourcesRoot, 'founder-relay', 'founder-node');
    default:
      return null;
  }
}

function defaultIsProcessAlive(pid: number): boolean {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === 'EPERM';
  }
}

function defaultProcessExecutablePath(
  pid: number,
  platform: NodeJS.Platform,
): string | null {
  try {
    if (platform === 'win32') {
      const command = `(Get-Process -Id ${pid} -ErrorAction Stop).Path`;
      return execFileSync(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', command],
        { encoding: 'utf8', windowsHide: true, timeout: 5_000 },
      ).trim() || null;
    }
    if (platform === 'linux') {
      return fs.readlinkSync(`/proc/${pid}/exe`);
    }
  } catch {
    return null;
  }
  return null;
}

function activeRelayPid(
  vaultRoot: string,
  executable: string,
  platform: NodeJS.Platform,
  isProcessAlive: (pid: number) => boolean,
  processExecutablePath: (pid: number, platform: NodeJS.Platform) => string | null,
  readLockFile: (file: string) => string,
): number | null {
  try {
    const lock = JSON.parse(
      readLockFile(path.join(vaultRoot, '.founder-node.lock')),
    ) as RelayLock;
    if (!lock.pid || !lock.exePath) return null;
    const normalize = (value: string): string => {
      const resolved = path.resolve(value);
      return platform === 'win32' ? resolved.toLowerCase() : resolved;
    };
    if (normalize(lock.exePath) !== normalize(executable)) return null;
    if (!isProcessAlive(lock.pid)) return null;
    const liveExecutable = processExecutablePath(lock.pid, platform);
    if (!liveExecutable || normalize(liveExecutable) !== normalize(executable)) {
      return null;
    }
    return lock.pid;
  } catch {
    return null;
  }
}

export function launchEmbeddedRelay(
  appRoot: string,
  platform: NodeJS.Platform = process.platform,
  deps: EmbeddedRelayDeps = {},
): EmbeddedRelayLaunchResult {
  const executable = embeddedRelayExecutable(
    appRoot,
    platform,
    deps.runtimeExecutable,
  );
  if (!executable) {
    return { state: 'unsupported', executable: null };
  }

  const existsSync = deps.existsSync ?? fs.existsSync;
  if (!existsSync(executable)) {
    return { state: 'not-bundled', executable };
  }

  const vaultRoot = path.join((deps.homedir ?? os.homedir)(), 'FounderVault');
  const runningPid = activeRelayPid(
    vaultRoot,
    executable,
    platform,
    deps.isProcessAlive ?? defaultIsProcessAlive,
    deps.processExecutablePath ?? defaultProcessExecutablePath,
    deps.readLockFile ?? ((file) => fs.readFileSync(file, 'utf8')),
  );
  if (runningPid) {
    return { state: 'already-running', executable, pid: runningPid };
  }

  const childEnv: NodeJS.ProcessEnv = {
    ...(deps.environment ?? process.env),
    FOUNDER_NODE_EMBEDDED: '1',
  };
  // VS Code extension hosts run Electron as Node. Let the embedded relay boot
  // as an Electron application instead of inheriting the host-only switch.
  delete childEnv.ELECTRON_RUN_AS_NODE;

  const child = (deps.spawnProcess ?? spawn)(
    executable,
    ['--embedded-founder-ide'],
    {
      detached: true,
      windowsHide: true,
      stdio: 'ignore',
      env: childEnv,
    },
  );
  child.unref();
  return { state: 'started', executable, pid: child.pid };
}
