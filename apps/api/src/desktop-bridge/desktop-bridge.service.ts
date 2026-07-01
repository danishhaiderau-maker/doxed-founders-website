import { Injectable } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import {
  sanitizeDesktopBridge,
  type BridgeWorkspace,
  type DesktopBridgeInput,
  type DesktopBridgeSnapshot,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

const BRIDGE_KEY = '_desktopBridgeByNode';
const WORKSPACES_KEY = '_workspacesByNode';
const MAX_WORKSPACES = 10;
const MAX_STR_LEN = 200;

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
}
