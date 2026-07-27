import { EventEmitter } from 'node:events';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  IPC_PROTOCOL_VERSION,
  type IdeWorkspaceEndpointPresence,
  type IpcMessage,
} from 'founder-ide-extension/ipc';
import type { BridgeSession, BridgeWorkspace } from '@dcf/utils';
import { IdeIpcClient } from './ide-ipc-client.js';

const ENDPOINT_ID_PATTERN = /^[0-9a-f-]{36}$/;
const ENDPOINT_TTL_MS = 60_000;

export interface WorkspaceClient {
  on(event: string, listener: (...args: any[]) => void): this;
  off(event: string, listener: (...args: any[]) => void): this;
  connect(): Promise<boolean>;
  disconnect(): void;
  isHandshakeActive(): boolean;
  send(message: IpcMessage): boolean;
}

interface RegistryEntry {
  presence: IdeWorkspaceEndpointPresence;
  client: WorkspaceClient;
}

export type WorkspaceClientFactory = (
  endpointId: string,
  vaultRoot: string,
) => WorkspaceClient;

export function ideWorkspaceEndpointDirectory(vaultRoot: string): string {
  return path.join(vaultRoot, 'ide-sessions');
}

export function founderWorkspaceId(workspacePath: string): string {
  const normalized = normalizeWorkspacePath(workspacePath);
  const digest = createHash('sha256').update(normalized).digest('hex').slice(0, 16);
  return `founder-workspace:${digest}`;
}

export function founderIdeSessionId(endpointId: string): string {
  return `founder-ide:${validateEndpointId(endpointId)}`;
}

export function readIdeWorkspaceEndpointPresences(
  vaultRoot: string,
  now = Date.now(),
  removeStale = true,
): IdeWorkspaceEndpointPresence[] {
  const directory = ideWorkspaceEndpointDirectory(vaultRoot);
  let names: string[];
  try {
    names = fs.readdirSync(directory);
  } catch {
    return [];
  }

  const found: IdeWorkspaceEndpointPresence[] = [];
  for (const name of names) {
    if (!name.endsWith('.json')) continue;
    const fileEndpointId = name.slice(0, -5);
    if (!ENDPOINT_ID_PATTERN.test(fileEndpointId)) continue;
    const file = path.join(directory, name);
    let remove = false;
    try {
      const raw = JSON.parse(
        fs.readFileSync(file, 'utf8'),
      ) as Partial<IdeWorkspaceEndpointPresence>;
      const heartbeat = typeof raw.heartbeatAt === 'string'
        ? Date.parse(raw.heartbeatAt)
        : Number.NaN;
      const started = typeof raw.startedAt === 'string'
        ? Date.parse(raw.startedAt)
        : Number.NaN;
      const workspacePath = typeof raw.workspacePath === 'string'
        ? path.resolve(raw.workspacePath)
        : null;
      const workspaceId = workspacePath
        ? founderWorkspaceId(workspacePath)
        : null;
      const valid =
        raw.version === 1
        && raw.protocolVersion === IPC_PROTOCOL_VERSION
        && raw.endpointId === fileEndpointId
        && typeof raw.workspaceName === 'string'
        && raw.workspaceName.trim().length > 0
        && typeof raw.processId === 'number'
        && Number.isInteger(raw.processId)
        && raw.processId > 0
        && Number.isFinite(started)
        && Number.isFinite(heartbeat)
        && heartbeat <= now + 30_000
        && now - heartbeat <= ENDPOINT_TTL_MS
        && raw.workspaceId === workspaceId
        && (
          raw.workspacePath === null
          || (
            typeof raw.workspacePath === 'string'
            && path.isAbsolute(raw.workspacePath)
          )
        );
      if (!valid) {
        remove = true;
      } else {
        found.push({
          version: 1,
          endpointId: fileEndpointId,
          workspaceId,
          workspacePath,
          workspaceName: raw.workspaceName!.trim().slice(0, 160),
          processId: raw.processId!,
          protocolVersion: IPC_PROTOCOL_VERSION,
          startedAt: raw.startedAt!,
          heartbeatAt: raw.heartbeatAt!,
        });
      }
    } catch {
      remove = true;
    }
    if (remove && removeStale) {
      try {
        fs.rmSync(file, { force: true });
      } catch {
        // The extension may be replacing this record; retry on the next scan.
      }
    }
  }

  return found.sort(
    (a, b) => Date.parse(b.heartbeatAt) - Date.parse(a.heartbeatAt),
  );
}

export class IdeIpcWorkspaceRegistry extends EventEmitter {
  private readonly entries = new Map<string, RegistryEntry>();
  private readonly clientFactory: WorkspaceClientFactory;

  constructor(
    private readonly vaultRoot: string,
    clientFactory: WorkspaceClientFactory = (endpointId, root) =>
      new IdeIpcClient({ endpointId, vaultRoot: root }),
  ) {
    super();
    this.clientFactory = clientFactory;
  }

  refresh(now = Date.now()): void {
    const presences = readIdeWorkspaceEndpointPresences(this.vaultRoot, now);
    const current = new Map(
      presences.map((presence) => [presence.endpointId, presence]),
    );

    for (const [endpointId, entry] of this.entries) {
      const presence = current.get(endpointId);
      if (presence) {
        entry.presence = presence;
        continue;
      }
      entry.client.disconnect();
      this.entries.delete(endpointId);
      this.emit('stateChanged');
    }

    for (const presence of presences) {
      if (this.entries.has(presence.endpointId)) continue;
      const client = this.clientFactory(presence.endpointId, this.vaultRoot);
      const entry: RegistryEntry = { presence, client };
      this.entries.set(presence.endpointId, entry);
      client.on('connected', () => this.emit('stateChanged'));
      client.on('disconnected', () => this.emit('stateChanged'));
      client.on('message', (message: IpcMessage) => {
        this.emit('message', presence.endpointId, message);
      });
      void client.connect().catch((error) => {
        console.warn(
          `[ide-ipc] workspace ${presence.endpointId} connect failed:`,
          error,
        );
      });
    }
  }

  hasActiveHandshake(): boolean {
    for (const entry of this.entries.values()) {
      if (entry.client.isHandshakeActive()) return true;
    }
    return false;
  }

  resolveSession(sessionId: string): WorkspaceClient | null {
    const prefix = 'founder-ide:';
    if (!sessionId.startsWith(prefix)) return null;
    const endpointId = sessionId.slice(prefix.length);
    if (!ENDPOINT_ID_PATTERN.test(endpointId)) return null;
    const entry = this.entries.get(endpointId);
    if (!entry?.client.isHandshakeActive()) return null;
    return entry.client;
  }

  sendToPreferred(message: IpcMessage): boolean {
    const candidates = [...this.entries.values()]
      .filter((entry) => entry.client.isHandshakeActive())
      .sort(
        (a, b) =>
          Date.parse(b.presence.heartbeatAt) - Date.parse(a.presence.heartbeatAt),
      );
    return candidates[0]?.client.send(message) ?? false;
  }

  sendToEndpoint(endpointId: string, message: IpcMessage): boolean {
    if (!ENDPOINT_ID_PATTERN.test(endpointId)) return false;
    const entry = this.entries.get(endpointId);
    if (!entry?.client.isHandshakeActive()) return false;
    return entry.client.send(message);
  }

  discoverSessions(): BridgeSession[] {
    return [...this.entries.values()]
      .filter(
        (entry) =>
          entry.client.isHandshakeActive()
          && entry.presence.workspacePath
          && entry.presence.workspaceId,
      )
      .map(({ presence }) => ({
        id: founderIdeSessionId(presence.endpointId),
        workspaceId: presence.workspaceId!,
        folderPath: presence.workspacePath!,
        title: presence.workspaceName,
        subtitle: 'Connected through Founder Node',
        repository: presence.workspacePath!,
        ideProvider: 'founder-ide',
        restorable: true,
        lastActiveAt: presence.heartbeatAt,
      }));
  }

  discoverWorkspaces(): BridgeWorkspace[] {
    return this.discoverSessions().map((session) => ({
      id: session.workspaceId!,
      title: session.title,
      repository: session.repository,
      branch: session.branch,
      ideProvider: 'founder-ide',
      lastActiveAt: session.lastActiveAt,
      hasActiveAgent: false,
    }));
  }

  disconnectAll(): void {
    for (const entry of this.entries.values()) {
      entry.client.disconnect();
    }
    this.entries.clear();
    this.emit('stateChanged');
  }
}

function validateEndpointId(endpointId: string): string {
  if (!ENDPOINT_ID_PATTERN.test(endpointId)) {
    throw new Error('Founder IDE endpoint id is invalid.');
  }
  return endpointId;
}

function normalizeWorkspacePath(value: string): string {
  const normalized = path.resolve(value).replace(/[\\/]+$/, '').replaceAll('\\', '/');
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

export const __testHooks = {
  ENDPOINT_ID_PATTERN,
  ENDPOINT_TTL_MS,
};
