import { Injectable } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import {
  sanitizeDesktopBridge,
  type DesktopBridgeInput,
  type DesktopBridgeSnapshot,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

const BRIDGE_KEY = '_desktopBridgeByNode';

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
}
