import { createHash, randomUUID } from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {
  IPC_PROTOCOL_VERSION,
  type IdeWorkspaceEndpointPresence,
} from './protocol.js';

const ENDPOINT_ID_PATTERN = /^[0-9a-f-]{36}$/;
const PRESENCE_DIRECTORY = 'ide-sessions';
const PRESENCE_TTL_MS = 60_000;

export interface WorkspaceEndpointOptions {
  workspacePath?: string | null;
  workspaceName?: string | null;
  vaultRoot?: string;
}

export function defaultEndpointVaultRoot(): string {
  return path.join(os.homedir(), 'FounderVault');
}

export function workspaceEndpointDirectory(
  vaultRoot = defaultEndpointVaultRoot(),
): string {
  return path.join(vaultRoot, PRESENCE_DIRECTORY);
}

export function normalizeWorkspacePath(value: string): string {
  const normalized = path.resolve(value).replace(/[\\/]+$/, '').replaceAll('\\', '/');
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

export function workspaceIdFor(workspacePath: string): string {
  const digest = createHash('sha256')
    .update(normalizeWorkspacePath(workspacePath))
    .digest('hex')
    .slice(0, 16);
  return `founder-workspace:${digest}`;
}

export function validateEndpointId(endpointId: string): string {
  if (!ENDPOINT_ID_PATTERN.test(endpointId)) {
    throw new Error('Founder IDE endpoint id is invalid.');
  }
  return endpointId;
}

export function pipePathForEndpoint(
  installId: string,
  endpointId: string,
): string {
  const safeEndpointId = validateEndpointId(endpointId);
  if (process.platform === 'win32') {
    return `\\\\.\\pipe\\founder-ide-${installId}-${safeEndpointId}`;
  }
  return `/tmp/founder-ide-${installId}-${safeEndpointId}.sock`;
}

export function createWorkspaceEndpointPresence(
  options: WorkspaceEndpointOptions,
  endpointId: string = randomUUID(),
  now = new Date(),
): IdeWorkspaceEndpointPresence {
  const workspacePath = options.workspacePath
    ? path.resolve(options.workspacePath)
    : null;
  return {
    version: 1,
    endpointId: validateEndpointId(endpointId),
    workspaceId: workspacePath ? workspaceIdFor(workspacePath) : null,
    workspacePath,
    workspaceName:
      options.workspaceName?.trim()
      || (workspacePath ? path.basename(workspacePath) : 'Founder IDE'),
    processId: process.pid,
    protocolVersion: IPC_PROTOCOL_VERSION,
    startedAt: now.toISOString(),
    heartbeatAt: now.toISOString(),
  };
}

export function writeWorkspaceEndpointPresence(
  presence: IdeWorkspaceEndpointPresence,
  vaultRoot = defaultEndpointVaultRoot(),
  now = new Date(),
): IdeWorkspaceEndpointPresence {
  const directory = workspaceEndpointDirectory(vaultRoot);
  fs.mkdirSync(directory, { recursive: true });
  const next = { ...presence, heartbeatAt: now.toISOString() };
  const destination = path.join(directory, `${validateEndpointId(next.endpointId)}.json`);
  const temporary = `${destination}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(next, null, 2), {
    encoding: 'utf8',
    mode: 0o600,
  });
  fs.renameSync(temporary, destination);
  return next;
}

export function removeWorkspaceEndpointPresence(
  endpointId: string,
  vaultRoot = defaultEndpointVaultRoot(),
): void {
  const file = path.join(
    workspaceEndpointDirectory(vaultRoot),
    `${validateEndpointId(endpointId)}.json`,
  );
  try {
    fs.rmSync(file, { force: true });
  } catch {
    // Best effort during extension shutdown.
  }
}

export function pruneStaleWorkspaceEndpointPresences(
  vaultRoot = defaultEndpointVaultRoot(),
  now = Date.now(),
): number {
  const directory = workspaceEndpointDirectory(vaultRoot);
  let names: string[];
  try {
    names = fs.readdirSync(directory);
  } catch {
    return 0;
  }

  let removed = 0;
  for (const name of names) {
    if (!name.endsWith('.json')) continue;
    const endpointId = name.slice(0, -5);
    if (!ENDPOINT_ID_PATTERN.test(endpointId)) continue;
    const file = path.join(directory, name);
    try {
      const raw = JSON.parse(fs.readFileSync(file, 'utf8')) as Partial<IdeWorkspaceEndpointPresence>;
      const heartbeat = typeof raw.heartbeatAt === 'string'
        ? Date.parse(raw.heartbeatAt)
        : Number.NaN;
      if (!Number.isFinite(heartbeat) || now - heartbeat > PRESENCE_TTL_MS) {
        fs.rmSync(file, { force: true });
        removed += 1;
      }
    } catch {
      // A malformed endpoint record cannot be trusted for routing.
      try {
        fs.rmSync(file, { force: true });
        removed += 1;
      } catch {
        // Leave it for the next bounded cleanup attempt.
      }
    }
  }
  return removed;
}

export const __testHooks = {
  ENDPOINT_ID_PATTERN,
  PRESENCE_TTL_MS,
};
