import { app } from 'electron';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { defaultVaultRoot } from './vault-manager';

const LOCK_FILE = '.founder-node.lock';

type LockRecord = {
  pid: number;
  exePath: string;
  startedAt: string;
};

function lockPath(vaultRoot: string): string {
  return path.join(vaultRoot, LOCK_FILE);
}

function isPidAlive(pid: number): boolean {
  if (!pid || pid === process.pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err: unknown) {
    const code = err && typeof err === 'object' && 'code' in err ? (err as NodeJS.ErrnoException).code : '';
    return code === 'EPERM';
  }
}

function readLock(vaultRoot: string): LockRecord | null {
  try {
    const raw = fs.readFileSync(lockPath(vaultRoot), 'utf8');
    const parsed = JSON.parse(raw) as LockRecord;
    if (typeof parsed.pid === 'number') return parsed;
  } catch {
    /* no lock */
  }
  return null;
}

function writeLock(vaultRoot: string): void {
  fs.mkdirSync(vaultRoot, { recursive: true });
  const record: LockRecord = {
    pid: process.pid,
    exePath: process.execPath,
    startedAt: new Date().toISOString(),
  };
  fs.writeFileSync(lockPath(vaultRoot), JSON.stringify(record, null, 2), 'utf8');
}

export function releaseGlobalInstanceLock(): void {
  const vaultRoot = defaultVaultRoot();
  const existing = readLock(vaultRoot);
  if (existing?.pid === process.pid) {
    try {
      fs.unlinkSync(lockPath(vaultRoot));
    } catch {
      /* already gone */
    }
  }
}

/** Windows: main process has no `--type=` in command line (gpu/utility children do). */
export function listMainFounderNodePids(): number[] {
  if (process.platform !== 'win32') return [];
  try {
    const script = [
      "Get-CimInstance Win32_Process -Filter \"Name = 'Founder Node.exe'\"",
      "| Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' }",
      '| Select-Object -ExpandProperty ProcessId',
    ].join(' ');
    const out = execSync(`powershell -NoProfile -Command "${script}"`, {
      encoding: 'utf8',
      windowsHide: true,
      timeout: 15_000,
    });
    return out
      .split(/\r?\n/)
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => Number.isFinite(n) && n > 0);
  } catch {
    return [];
  }
}

export function terminateOtherMainFounderNodeProcesses(): number[] {
  const killed: number[] = [];
  for (const pid of listMainFounderNodePids()) {
    if (pid === process.pid) continue;
    try {
      process.kill(pid);
      killed.push(pid);
    } catch {
      try {
        execSync(`taskkill /PID ${pid} /T /F`, { windowsHide: true, timeout: 10_000 });
        killed.push(pid);
      } catch {
        /* still running */
      }
    }
  }
  return killed;
}

export type GlobalLockResult = 'acquired' | 'stale-replaced' | 'blocked';

/**
 * File lock in ~/FounderVault — works across portable + installed .exe paths.
 * Call after terminating duplicate mains when packaged.
 */
export function acquireGlobalInstanceLock(): GlobalLockResult {
  const vaultRoot = defaultVaultRoot();
  const existing = readLock(vaultRoot);
  if (existing && existing.pid !== process.pid && isPidAlive(existing.pid)) {
    return 'blocked';
  }
  writeLock(vaultRoot);
  return existing ? 'stale-replaced' : 'acquired';
}

export function enforceSingleFounderNodeInstance(): boolean {
  if (!app.isPackaged) return true;

  const killed = terminateOtherMainFounderNodeProcesses();
  if (killed.length) {
    console.log(`Founder Node: stopped ${killed.length} duplicate instance(s): ${killed.join(', ')}`);
  }

  const lock = acquireGlobalInstanceLock();
  if (lock === 'blocked') {
    const others = listMainFounderNodePids().filter((p) => p !== process.pid);
    if (others.length) {
      terminateOtherMainFounderNodeProcesses();
      if (acquireGlobalInstanceLock() !== 'blocked') return true;
    }
    console.warn('Founder Node: another instance is already running — exiting.');
    return false;
  }
  return true;
}
