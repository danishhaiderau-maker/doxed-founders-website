import { createHash, randomUUID } from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

export const AGENT_PATH_LEASE_TTL_MS = 3 * 60_000;

export interface FounderPathLease {
  version: 1;
  taskId: string;
  workspacePath: string;
  relativePath: string;
  fencingToken: string;
  claimedAt: string;
  heartbeatAt: string;
}

export type FounderPathClaimResult =
  | { ok: true; lease: FounderPathLease }
  | { ok: false; ownerTaskId: string; relativePath: string; reason: string };

export type FounderPathValidationResult =
  | { ok: true }
  | { ok: false; reason: string };

export class FounderPathLeaseStore {
  constructor(
    private readonly root = path.join(os.homedir(), '.founder-ide', 'coordination', 'claims'),
    private readonly ttlMs = AGENT_PATH_LEASE_TTL_MS,
  ) {}

  claim(workspacePath: string, targetPath: string, taskId: string, now = Date.now()): FounderPathClaimResult {
    const normalized = normalizeTarget(workspacePath, targetPath);
    if (!normalized) {
      return {
        ok: false,
        ownerTaskId: '',
        relativePath: targetPath,
        reason: 'The requested path is outside the active workspace.',
      };
    }

    fs.mkdirSync(this.root, { recursive: true });
    const file = this.claimFile(normalized.workspacePath, normalized.relativePath);
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const lease = this.read(file);
      if (lease) {
        if (lease.taskId === taskId) {
          const refreshed = { ...lease, heartbeatAt: new Date(now).toISOString() };
          this.replace(file, refreshed);
          return { ok: true, lease: refreshed };
        }
        if (isFresh(lease, now, this.ttlMs)) {
          return {
            ok: false,
            ownerTaskId: lease.taskId,
            relativePath: lease.relativePath,
            reason: `${lease.relativePath} is currently claimed by another active Founder task.`,
          };
        }
        try { fs.rmSync(file, { force: true }); } catch { /* raced with the owner */ }
      }

      const timestamp = new Date(now).toISOString();
      const next: FounderPathLease = {
        version: 1,
        taskId,
        workspacePath: normalized.workspacePath,
        relativePath: normalized.relativePath,
        fencingToken: `${now.toString(36)}-${randomUUID()}`,
        claimedAt: timestamp,
        heartbeatAt: timestamp,
      };
      try {
        const fd = fs.openSync(file, 'wx', 0o600);
        try { fs.writeFileSync(fd, JSON.stringify(next, null, 2), 'utf8'); } finally { fs.closeSync(fd); }
        return { ok: true, lease: next };
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
      }
    }

    const owner = this.read(file);
    return {
      ok: false,
      ownerTaskId: owner?.taskId ?? '',
      relativePath: normalized.relativePath,
      reason: `${normalized.relativePath} could not be claimed because its ownership changed concurrently.`,
    };
  }

  validate(lease: FounderPathLease, now = Date.now()): FounderPathValidationResult {
    const current = this.read(this.claimFile(lease.workspacePath, lease.relativePath));
    if (!current) return { ok: false, reason: `${lease.relativePath} no longer has an active path claim.` };
    if (!isFresh(current, now, this.ttlMs)) {
      return { ok: false, reason: `${lease.relativePath} path claim expired before the edit was applied.` };
    }
    if (current.taskId !== lease.taskId || current.fencingToken !== lease.fencingToken) {
      return { ok: false, reason: `${lease.relativePath} ownership changed before the edit was applied.` };
    }
    return { ok: true };
  }

  refreshTask(taskId: string, now = Date.now()): void {
    for (const file of this.files()) {
      const lease = this.read(file);
      if (!lease || lease.taskId !== taskId) continue;
      this.replace(file, { ...lease, heartbeatAt: new Date(now).toISOString() });
    }
  }

  claimsForTask(taskId: string, now = Date.now()): FounderPathLease[] {
    return this.files()
      .map((file) => this.read(file))
      .filter((lease): lease is FounderPathLease => Boolean(
        lease && lease.taskId === taskId && isFresh(lease, now, this.ttlMs),
      ));
  }

  releaseTask(taskId: string): void {
    for (const file of this.files()) {
      const lease = this.read(file);
      if (!lease || lease.taskId !== taskId) continue;
      try { fs.rmSync(file, { force: true }); } catch { /* best effort */ }
    }
  }

  release(lease: FounderPathLease): void {
    const file = this.claimFile(lease.workspacePath, lease.relativePath);
    const current = this.read(file);
    if (!current) return;
    if (current.taskId !== lease.taskId || current.fencingToken !== lease.fencingToken) return;
    try { fs.rmSync(file, { force: true }); } catch { /* best effort */ }
  }

  prune(now = Date.now()): void {
    for (const file of this.files()) {
      const lease = this.read(file);
      if (lease && isFresh(lease, now, this.ttlMs)) continue;
      try { fs.rmSync(file, { force: true }); } catch { /* best effort */ }
    }
  }

  private claimFile(workspacePath: string, relativePath: string): string {
    const digest = createHash('sha256')
      .update(workspacePath)
      .update('\0')
      .update(relativePath)
      .digest('hex');
    return path.join(this.root, `${digest}.json`);
  }

  private files(): string[] {
    try {
      return fs.readdirSync(this.root)
        .filter((name) => name.endsWith('.json'))
        .map((name) => path.join(this.root, name));
    } catch {
      return [];
    }
  }

  private read(file: string): FounderPathLease | null {
    try { return parsePathLease(JSON.parse(fs.readFileSync(file, 'utf8'))); } catch { return null; }
  }

  private replace(file: string, lease: FounderPathLease): void {
    // Keep the claim inode present while refreshing. A transient partial read
    // can only fail closed; removing the file would create an unsafe takeover
    // window on Windows where rename-over-existing is not atomic.
    fs.writeFileSync(file, JSON.stringify(lease, null, 2), { encoding: 'utf8', mode: 0o600 });
  }
}

function normalizeTarget(workspacePath: string, targetPath: string): { workspacePath: string; relativePath: string } | null {
  const root = path.resolve(workspacePath);
  const target = path.isAbsolute(targetPath) ? path.resolve(targetPath) : path.resolve(root, targetPath);
  const relative = path.relative(root, target);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) return null;
  return {
    workspacePath: root.replaceAll('\\', '/').replace(/\/$/, '').toLowerCase(),
    relativePath: relative.replaceAll('\\', '/').toLowerCase(),
  };
}

function isFresh(lease: FounderPathLease, now: number, ttlMs: number): boolean {
  const heartbeat = Date.parse(lease.heartbeatAt);
  return Number.isFinite(heartbeat) && now - heartbeat <= ttlMs;
}

export function parsePathLease(value: unknown): FounderPathLease | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const input = value as Partial<FounderPathLease>;
  if (
    input.version !== 1
    || typeof input.taskId !== 'string'
    || typeof input.workspacePath !== 'string'
    || typeof input.relativePath !== 'string'
    || typeof input.fencingToken !== 'string'
    || typeof input.claimedAt !== 'string'
    || typeof input.heartbeatAt !== 'string'
  ) return null;
  return input as FounderPathLease;
}

export const founderPathLeases = new FounderPathLeaseStore();
