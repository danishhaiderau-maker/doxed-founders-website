import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import type { BridgeAgent, BridgeSession, BridgeWorkspace } from '@dcf/utils';

const PRESENCE_TTL_MS = 3 * 60_000;

interface FounderIdePresence {
  version: 1;
  id: string;
  workspacePath: string;
  workspaceName: string;
  branch?: string;
  title: string;
  provider: string;
  status: 'working' | 'waiting';
  ownedFiles: string[];
  startedAt: string;
  heartbeatAt: string;
}

export function founderIdeCoordinationRoot(): string {
  return path.join(os.homedir(), '.founder-ide', 'coordination');
}

export function readFounderIdePresences(
  root = founderIdeCoordinationRoot(),
  now = Date.now(),
): FounderIdePresence[] {
  let files: string[];
  try {
    files = fs.readdirSync(root).filter((name) => name.endsWith('.json'));
  } catch {
    return [];
  }

  const found: FounderIdePresence[] = [];
  for (const name of files) {
    try {
      const raw = JSON.parse(fs.readFileSync(path.join(root, name), 'utf8')) as Partial<FounderIdePresence>;
      const heartbeat = typeof raw.heartbeatAt === 'string' ? Date.parse(raw.heartbeatAt) : Number.NaN;
      if (
        raw.version !== 1
        || typeof raw.id !== 'string'
        || typeof raw.workspacePath !== 'string'
        || typeof raw.workspaceName !== 'string'
        || typeof raw.title !== 'string'
        || typeof raw.provider !== 'string'
        || (raw.status !== 'working' && raw.status !== 'waiting')
        || !Array.isArray(raw.ownedFiles)
        || typeof raw.startedAt !== 'string'
        || !Number.isFinite(heartbeat)
        || now - heartbeat > PRESENCE_TTL_MS
      ) continue;
      found.push({
        ...raw,
        version: 1,
        ownedFiles: raw.ownedFiles.filter((file): file is string => typeof file === 'string').slice(0, 80),
      } as FounderIdePresence);
    } catch {
      // The IDE may be atomically replacing its lease while this scan runs.
    }
  }
  return found.sort((a, b) => Date.parse(b.heartbeatAt) - Date.parse(a.heartbeatAt));
}

export function discoverFounderIdeAgents(root?: string): BridgeAgent[] {
  return readFounderIdePresences(root).map((presence) => ({
    id: `founder-ide:${presence.id}`,
    label: 'Founder AI',
    status: presence.status === 'working' ? 'running' : 'waiting',
    task: presence.title,
    startedAt: presence.startedAt,
  }));
}

export function discoverFounderIdeAgentSessions(root?: string): BridgeSession[] {
  return readFounderIdePresences(root).map((presence) => ({
    id: `founder-agent:${presence.id}`,
    workspaceId: workspaceId(presence.workspacePath),
    folderPath: presence.workspacePath,
    title: presence.title,
    subtitle: `${presence.provider} | ${presence.ownedFiles.length} open file${presence.ownedFiles.length === 1 ? '' : 's'}`,
    repository: presence.workspacePath,
    branch: presence.branch,
    ideProvider: 'founder-ide',
    restorable: false,
    lastActiveAt: presence.heartbeatAt,
    messageCount: 1,
    filesChangedCount: presence.ownedFiles.length,
    isAgentProject: true,
    agentTyping: presence.status === 'working',
  }));
}

export function discoverFounderIdeAgentWorkspaces(root?: string): BridgeWorkspace[] {
  const byPath = new Map<string, FounderIdePresence>();
  for (const presence of readFounderIdePresences(root)) {
    const key = normalizedPath(presence.workspacePath);
    if (!byPath.has(key)) byPath.set(key, presence);
  }
  return [...byPath.values()].map((presence) => ({
    id: workspaceId(presence.workspacePath),
    title: presence.workspaceName,
    repository: presence.workspacePath,
    branch: presence.branch,
    ideProvider: 'founder-ide',
    lastActiveAt: presence.heartbeatAt,
    hasActiveAgent: true,
  }));
}

function workspaceId(workspacePath: string): string {
  const digest = createHash('sha256').update(normalizedPath(workspacePath)).digest('hex').slice(0, 16);
  return `founder-workspace:${digest}`;
}

function normalizedPath(value: string): string {
  return value.trim().replaceAll('\\', '/').replace(/\/$/, '').toLowerCase();
}

export const __testHooks = { PRESENCE_TTL_MS, workspaceId };
