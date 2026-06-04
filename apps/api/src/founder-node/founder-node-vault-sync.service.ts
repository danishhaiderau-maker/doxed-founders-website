import { Injectable } from '@nestjs/common';
import { FounderNodeSyncJobKind, Prisma } from '@prisma/client';
import type { VaultMergePatch } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { FounderNodeSyncService } from './founder-node-sync.service';

const ONLINE_WINDOW_MS = 5 * 60 * 1000;
const DESKTOP_PLATFORMS = new Set(['win32', 'darwin', 'linux', 'windows', 'desktop']);

function isDesktopPlatform(platform: string | null | undefined): boolean {
  if (!platform) return true;
  const p = platform.toLowerCase();
  return DESKTOP_PLATFORMS.has(p) || p.includes('win') || p.includes('mac') || p.includes('linux');
}

function isMobilePlatform(platform: string | null | undefined): boolean {
  if (!platform) return false;
  const p = platform.toLowerCase();
  return p === 'android' || p === 'ios' || p === 'mobile';
}

@Injectable()
export class FounderNodeVaultSyncService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly syncJobs: FounderNodeSyncService,
  ) {}

  async recordRelayMerge(
    userId: string,
    nodeId: string,
    input: {
      label?: string | null;
      platform?: string | null;
      mergePatch?: VaultMergePatch | null;
      fileManifest?: Record<string, string> | null;
    },
  ): Promise<{ vaultSyncVersion: number }> {
    const existing = await this.prisma.founderNodeVaultRelay.findUnique({ where: { nodeId } });
    const nextVersion = (existing?.vaultSyncVersion ?? 0) + (input.mergePatch ? 1 : 0);

    if (input.mergePatch) {
      const storedPatch = {
        ...input.mergePatch,
        vaultSyncVersion: nextVersion,
      };
      await this.prisma.founderNodeVaultRelay.updateMany({
        where: { userId, nodeId },
        data: {
          vaultSyncVersion: nextVersion,
          mergePatch: storedPatch as Prisma.InputJsonValue,
          fileManifest: (input.fileManifest ?? input.mergePatch.fileManifest) as Prisma.InputJsonValue,
          label: input.label ?? undefined,
          platform: input.platform ?? undefined,
        },
      });
      await this.scheduleDesktopPullIfNeeded(userId, nodeId, nextVersion, input.mergePatch);
      return { vaultSyncVersion: nextVersion };
    }

    return { vaultSyncVersion: existing?.vaultSyncVersion ?? 0 };
  }

  async getVaultSyncPlan(userId: string, requestingNodeId: string) {
    const relays = await this.prisma.founderNodeVaultRelay.findMany({
      where: { userId },
      orderBy: { updatedAt: 'desc' },
    });
    const acks = await this.prisma.founderNodeVaultSyncAck.findMany({
      where: { userId, nodeId: requestingNodeId },
    });
    const ackBySource = new Map(acks.map((a) => [a.sourceNodeId, a.vaultSyncVersion]));

    const pulls = relays
      .filter((r) => r.nodeId !== requestingNodeId && r.mergePatch != null && r.vaultSyncVersion > 0)
      .filter((r) => r.vaultSyncVersion > (ackBySource.get(r.nodeId) ?? 0))
      .map((r) => ({
        sourceNodeId: r.nodeId,
        sourceLabel: r.label,
        sourcePlatform: r.platform,
        vaultSyncVersion: r.vaultSyncVersion,
        updatedAt: r.updatedAt.toISOString(),
      }));

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const primary = settings?.vaultPrimaryPlatform;
    const vaultPrimaryPlatform =
      primary === 'desktop' || primary === 'mobile' ? primary : null;

    return { pulls, vaultPrimaryPlatform };
  }

  async getMergePatch(userId: string, requestingNodeId: string, sourceNodeId: string) {
    const relay = await this.prisma.founderNodeVaultRelay.findFirst({
      where: { userId, nodeId: sourceNodeId },
    });
    if (!relay?.mergePatch) {
      return { mergePatch: null, vaultSyncVersion: 0 };
    }

    const ack = await this.prisma.founderNodeVaultSyncAck.findUnique({
      where: { nodeId_sourceNodeId: { nodeId: requestingNodeId, sourceNodeId } },
    });
    if (ack && ack.vaultSyncVersion >= relay.vaultSyncVersion) {
      return { mergePatch: null, vaultSyncVersion: relay.vaultSyncVersion, alreadyApplied: true };
    }

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const primary = settings?.vaultPrimaryPlatform;
    const vaultPrimaryPlatform =
      primary === 'desktop' || primary === 'mobile' ? primary : null;

    return {
      mergePatch: relay.mergePatch as VaultMergePatch,
      vaultSyncVersion: relay.vaultSyncVersion,
      vaultPrimaryPlatform,
      alreadyApplied: false,
    };
  }

  async ackMerge(
    userId: string,
    nodeId: string,
    sourceNodeId: string,
    vaultSyncVersion: number,
  ) {
    await this.prisma.founderNodeVaultSyncAck.upsert({
      where: { nodeId_sourceNodeId: { nodeId, sourceNodeId } },
      create: { userId, nodeId, sourceNodeId, vaultSyncVersion },
      update: { vaultSyncVersion, updatedAt: new Date() },
    });
    return { success: true };
  }

  async onNodeHeartbeat(userId: string, nodeId: string) {
    const node = await this.prisma.founderNode.findUnique({ where: { nodeId } });
    if (!node || !isDesktopPlatform(node.platform)) return;
    const relays = await this.prisma.founderNodeVaultRelay.findMany({
      where: { userId },
    });
    for (const relay of relays) {
      if (relay.nodeId === nodeId || !relay.mergePatch) continue;
      if (isDesktopPlatform(relay.platform) && relay.nodeId !== nodeId) continue;
      const ack = await this.prisma.founderNodeVaultSyncAck.findUnique({
        where: { nodeId_sourceNodeId: { nodeId, sourceNodeId: relay.nodeId } },
      });
      if ((ack?.vaultSyncVersion ?? 0) >= relay.vaultSyncVersion) continue;
      await this.syncJobs.enqueuePullVaultMerge(userId, nodeId, {
        sourceNodeId: relay.nodeId,
        vaultSyncVersion: relay.vaultSyncVersion,
        mergePatch: relay.mergePatch as VaultMergePatch,
      });
    }
  }

  private async scheduleDesktopPullIfNeeded(
    userId: string,
    sourceNodeId: string,
    vaultSyncVersion: number,
    mergePatch: VaultMergePatch,
  ) {
    const source = await this.prisma.founderNode.findUnique({ where: { nodeId: sourceNodeId } });
    if (!source || !isMobilePlatform(source.platform)) return;

    const nodes = await this.prisma.founderNode.findMany({ where: { userId } });
    const desktop = nodes.find(
      (n) =>
        n.nodeId !== sourceNodeId &&
        isDesktopPlatform(n.platform) &&
        n.lastSeenAt != null &&
        Date.now() - n.lastSeenAt.getTime() < ONLINE_WINDOW_MS,
    );
    if (!desktop) return;

    const ack = await this.prisma.founderNodeVaultSyncAck.findUnique({
      where: {
        nodeId_sourceNodeId: { nodeId: desktop.nodeId, sourceNodeId },
      },
    });
    if ((ack?.vaultSyncVersion ?? 0) >= vaultSyncVersion) return;

    await this.syncJobs.enqueuePullVaultMerge(userId, desktop.nodeId, {
      sourceNodeId,
      vaultSyncVersion,
      mergePatch,
    });
  }
}
