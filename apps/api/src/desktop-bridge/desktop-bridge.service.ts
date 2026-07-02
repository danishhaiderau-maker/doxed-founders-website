import { Injectable } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import {
  sanitizeDesktopBridge,
  type BridgeMessage,
  type BridgeSession,
  type BridgeWorkspace,
  type DesktopBridgeInput,
  type DesktopBridgeSnapshot,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

const BRIDGE_KEY = '_desktopBridgeByNode';
const WORKSPACES_KEY = '_workspacesByNode';
const SESSIONS_KEY = '_sessionsByNode';
const MAX_WORKSPACES = 10;
const MAX_SESSIONS = 20;
const MAX_STR_LEN = 200;
const MAX_MESSAGES_PER_SESSION = 30;
const MESSAGE_TEXT_MAX = 1000;

@Injectable()
export class DesktopBridgeService {
  constructor(private readonly prisma: PrismaService) {}

  async saveFromHeartbeat(
    userId: string,
    nodeId: string,
    label: string,
    bridge: DesktopBridgeInput | null | undefined,
  ): Promise<DesktopBridgeSnapshot | null> {
    const snapshot = sanitizeDesktopBridge(nodeId, label, bridge);
    if (!snapshot) return null;

    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const base =
      settings?.memoryGraph && typeof settings.memoryGraph === 'object' && !Array.isArray(settings.memoryGraph)
        ? { ...(settings.memoryGraph as Record<string, unknown>) }
        : {};

    const byNode =
      base[BRIDGE_KEY] && typeof base[BRIDGE_KEY] === 'object' && !Array.isArray(base[BRIDGE_KEY])
        ? { ...(base[BRIDGE_KEY] as Record<string, DesktopBridgeSnapshot>) }
        : {};
    byNode[nodeId] = snapshot;
    base[BRIDGE_KEY] = byNode;

    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId, memoryGraph: base as Prisma.InputJsonValue },
      update: { memoryGraph: base as Prisma.InputJsonValue },
    });

    return snapshot;
  }

  async getLatest(userId: string): Promise<DesktopBridgeSnapshot | null> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const graph = settings?.memoryGraph;
    if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return null;
    const byNode = (graph as Record<string, unknown>)[BRIDGE_KEY];
    if (!byNode || typeof byNode !== 'object' || Array.isArray(byNode)) return null;

    const snapshots = Object.values(byNode as Record<string, DesktopBridgeSnapshot>).filter(
      (s) => s && typeof s.updatedAt === 'string',
    );
    if (snapshots.length === 0) return null;
    snapshots.sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );
    return snapshots[0]!;
  }

  async listForUser(userId: string): Promise<DesktopBridgeSnapshot[]> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const graph = settings?.memoryGraph;
    if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return [];
    const byNode = (graph as Record<string, unknown>)[BRIDGE_KEY];
    if (!byNode || typeof byNode !== 'object' || Array.isArray(byNode)) return [];
    return Object.values(byNode as Record<string, DesktopBridgeSnapshot>).sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );
  }

  /**
   * Persist the full workspaces[] array sent by Founder Node v0.6.0+ in
   * every heartbeat. Stored under `memoryGraph._workspacesByNode[nodeId]` so
   * it lives alongside the legacy single-workspace desktop bridge snapshot
   * without requiring a Prisma schema migration.
   */
  async saveWorkspaces(
    userId: string,
    nodeId: string,
    workspaces: BridgeWorkspace[],
  ): Promise<BridgeWorkspace[]> {
    const cleaned = this.sanitizeWorkspaces(workspaces);
    if (cleaned.length === 0) return [];

    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const base =
      settings?.memoryGraph && typeof settings.memoryGraph === 'object' && !Array.isArray(settings.memoryGraph)
        ? { ...(settings.memoryGraph as Record<string, unknown>) }
        : {};

    const byNode =
      base[WORKSPACES_KEY] && typeof base[WORKSPACES_KEY] === 'object' && !Array.isArray(base[WORKSPACES_KEY])
        ? { ...(base[WORKSPACES_KEY] as Record<string, BridgeWorkspace[]>) }
        : {};
    byNode[nodeId] = cleaned;
    base[WORKSPACES_KEY] = byNode;

    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId, memoryGraph: base as Prisma.InputJsonValue },
      update: { memoryGraph: base as Prisma.InputJsonValue },
    });

    return cleaned;
  }

  /**
   * Return the most recent workspaces[] persisted across all of a user's
   * Founder Nodes, newest first. Empty array when no node has reported yet.
   */
  async listWorkspaces(userId: string): Promise<BridgeWorkspace[]> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const graph = settings?.memoryGraph;
    if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return [];
    const byNode = (graph as Record<string, unknown>)[WORKSPACES_KEY];
    if (!byNode || typeof byNode !== 'object' || Array.isArray(byNode)) return [];

    const perNode = Object.values(byNode as Record<string, BridgeWorkspace[]>).filter(
      Array.isArray,
    ) as BridgeWorkspace[][];
    if (perNode.length === 0) return [];

    // Flatten all nodes' workspaces, then sort by lastActiveAt desc.
    const all = perNode.flat();
    all.sort(
      (a, b) =>
        new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
    );
    return all.slice(0, MAX_WORKSPACES * 4);
  }

  private sanitizeWorkspaces(input: unknown): BridgeWorkspace[] {
    if (!Array.isArray(input)) return [];
    const out: BridgeWorkspace[] = [];
    for (const raw of input) {
      if (!raw || typeof raw !== 'object') continue;
      const w = raw as Record<string, unknown>;
      const id = typeof w.id === 'string' ? w.id.trim() : '';
      const title = typeof w.title === 'string' ? w.title.trim() : '';
      if (!id || !title) continue;
      const lastActiveAt =
        typeof w.lastActiveAt === 'string' && !Number.isNaN(Date.parse(w.lastActiveAt))
          ? w.lastActiveAt
          : new Date().toISOString();
      out.push({
        id,
        title: title.slice(0, MAX_STR_LEN),
        repository: this.optString(w.repository),
        branch: this.optString(w.branch),
        ideProvider:
          typeof w.ideProvider === 'string' && w.ideProvider.trim()
            ? w.ideProvider.trim().slice(0, 40)
            : 'cursor',
        lastActiveAt,
        hasActiveAgent: Boolean(w.hasActiveAgent),
        messageCount:
          typeof w.messageCount === 'number' && Number.isFinite(w.messageCount)
            ? Math.max(0, Math.floor(w.messageCount))
            : undefined,
      });
      if (out.length >= MAX_WORKSPACES) break;
    }
    return out;
  }

  private optString(v: unknown): string | undefined {
    return typeof v === 'string' && v.trim() ? v.trim().slice(0, MAX_STR_LEN) : undefined;
  }

  /**
   * Persist the real Cursor chat/agent sessions[] sent by Founder Node v0.6.1+
   * in every heartbeat. Stored under `memoryGraph._sessionsByNode[nodeId]` so
   * it lives alongside the workspaces array without a Prisma schema migration.
   */
  async saveSessions(
    userId: string,
    nodeId: string,
    sessions: BridgeSession[],
  ): Promise<BridgeSession[]> {
    const cleaned = this.sanitizeSessions(sessions);
    if (cleaned.length === 0) return [];

    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const base =
      settings?.memoryGraph && typeof settings.memoryGraph === 'object' && !Array.isArray(settings.memoryGraph)
        ? { ...(settings.memoryGraph as Record<string, unknown>) }
        : {};

    const byNode =
      base[SESSIONS_KEY] && typeof base[SESSIONS_KEY] === 'object' && !Array.isArray(base[SESSIONS_KEY])
        ? { ...(base[SESSIONS_KEY] as Record<string, BridgeSession[]>) }
        : {};
    byNode[nodeId] = cleaned;
    base[SESSIONS_KEY] = byNode;

    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId, memoryGraph: base as Prisma.InputJsonValue },
      update: { memoryGraph: base as Prisma.InputJsonValue },
    });

    return cleaned;
  }

  /**
   * Return the most recent sessions[] persisted across all of a user's
   * Founder Nodes, newest first. Empty array when no node has reported yet.
   */
  async listSessions(userId: string): Promise<BridgeSession[]> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const graph = settings?.memoryGraph;
    if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return [];
    const byNode = (graph as Record<string, unknown>)[SESSIONS_KEY];
    if (!byNode || typeof byNode !== 'object' || Array.isArray(byNode)) return [];

    const perNode = Object.values(byNode as Record<string, BridgeSession[]>).filter(
      Array.isArray,
    ) as BridgeSession[][];
    if (perNode.length === 0) return [];

    const all = perNode.flat();
    all.sort(
      (a, b) =>
        new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
    );
    return all.slice(0, MAX_SESSIONS * 2);
  }

  private sanitizeSessions(input: unknown): BridgeSession[] {
    if (!Array.isArray(input)) return [];
    const out: BridgeSession[] = [];
    for (const raw of input) {
      if (!raw || typeof raw !== 'object') continue;
      const s = raw as Record<string, unknown>;
      const id = typeof s.id === 'string' ? s.id.trim() : '';
      const title = typeof s.title === 'string' ? s.title.trim() : '';
      if (!id || !title) continue;
      const lastActiveAt =
        typeof s.lastActiveAt === 'string' && !Number.isNaN(Date.parse(s.lastActiveAt))
          ? s.lastActiveAt
          : new Date().toISOString();
      out.push({
        id,
        title: title.slice(0, MAX_STR_LEN),
        subtitle: this.optString(s.subtitle),
        workspaceId: this.optString(s.workspaceId),
        repository: this.optString(s.repository),
        branch: this.optString(s.branch),
        ideProvider: this.optString(s.ideProvider) ?? 'cursor',
        restorable: Boolean(s.restorable),
        lastActiveAt,
        messages: this.sanitizeMessages(s.messages),
        messageCount:
          typeof s.messageCount === 'number' && Number.isFinite(s.messageCount)
            ? Math.max(0, Math.floor(s.messageCount))
            : undefined,
        totalLinesAdded:
          typeof s.totalLinesAdded === 'number' && Number.isFinite(s.totalLinesAdded)
            ? Math.max(0, Math.floor(s.totalLinesAdded))
            : undefined,
        totalLinesRemoved:
          typeof s.totalLinesRemoved === 'number' && Number.isFinite(s.totalLinesRemoved)
            ? Math.max(0, Math.floor(s.totalLinesRemoved))
            : undefined,
        filesChangedCount:
          typeof s.filesChangedCount === 'number' && Number.isFinite(s.filesChangedCount)
            ? Math.max(0, Math.floor(s.filesChangedCount))
            : undefined,
        isAgentProject: Boolean(s.isAgentProject) || undefined,
      });
      if (out.length >= MAX_SESSIONS) break;
    }
    return out;
  }

  private sanitizeMessages(input: unknown): BridgeMessage[] | undefined {
    if (!Array.isArray(input)) return undefined;
    const out: BridgeMessage[] = [];
    for (const raw of input) {
      if (!raw || typeof raw !== 'object') continue;
      const m = raw as Record<string, unknown>;
      const role = m.role;
      const content = typeof m.content === 'string' ? m.content : '';
      if (role !== 'user' && role !== 'assistant' && role !== 'system') continue;
      if (!content.trim()) continue;
      const ts =
        typeof m.timestamp === 'string' && !Number.isNaN(Date.parse(m.timestamp))
          ? m.timestamp
          : undefined;
      out.push({
        role,
        content: content.slice(0, MESSAGE_TEXT_MAX),
        ...(ts ? { timestamp: ts } : {}),
        ...(typeof m.model === 'string' && m.model.trim()
          ? { model: m.model.trim().slice(0, 60) }
          : {}),
      });
      if (out.length >= MAX_MESSAGES_PER_SESSION) break;
    }
    return out.length > 0 ? out : undefined;
  }
}
