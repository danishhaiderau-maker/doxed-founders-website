import {
  BadRequestException,
  Inject,
  Injectable,
  NotFoundException,
  UnauthorizedException,
  forwardRef,
} from '@nestjs/common';
import type { DeviceMemoryPayload } from '@dcf/utils';
import type { FounderNodeHeartbeat } from '@dcf/founder-vault';
import * as bcrypt from 'bcrypt';
import { randomBytes } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import { FounderCopilotService } from '../events/founder-copilot.service';
import { FounderNodeVaultSyncService } from './founder-node-vault-sync.service';
import type { VaultMergePatch } from '@dcf/utils';

const PAIRING_TTL_MS = 30 * 60 * 1000;
const ONLINE_WINDOW_MS = 5 * 60 * 1000;

@Injectable()
export class FounderNodeService {
  constructor(
    private readonly prisma: PrismaService,
    @Inject(forwardRef(() => FounderCopilotService))
    private readonly copilot: FounderCopilotService,
    private readonly vaultSync: FounderNodeVaultSyncService,
  ) {}

  async createPairingCode(userId: string, targetPlatform?: 'desktop' | 'mobile') {
    await this.prisma.founderNodePairingCode.deleteMany({
      where: { userId, usedAt: null, expiresAt: { lt: new Date() } },
    });

    const code = this.generatePairingCode();
    const expiresAt = new Date(Date.now() + PAIRING_TTL_MS);
    const normalizedTarget =
      targetPlatform === 'mobile' || targetPlatform === 'desktop' ? targetPlatform : null;

    await this.prisma.founderNodePairingCode.create({
      data: { userId, code, expiresAt, targetPlatform: normalizedTarget },
    });

    return {
      code,
      expiresAt: expiresAt.toISOString(),
      targetPlatform: normalizedTarget,
    };
  }

  async getStatus(userId: string) {
    const nodes = await this.prisma.founderNode.findMany({
      where: { userId },
      orderBy: { lastSeenAt: 'desc' },
    });

    return {
      nodes: nodes.map((n) => this.toStatusRow(n)),
    };
  }

  async revokeNode(userId: string, nodeId: string) {
    const node = await this.prisma.founderNode.findFirst({
      where: { userId, nodeId },
    });
    if (!node) throw new NotFoundException('Node not found');
    await this.prisma.founderNodeVaultRelay.deleteMany({ where: { nodeId: node.nodeId } });
    await this.prisma.founderNodeVaultSyncAck.deleteMany({
      where: { OR: [{ nodeId: node.nodeId }, { sourceNodeId: node.nodeId }] },
    });
    await this.prisma.founderNode.delete({ where: { id: node.id } });
    return { success: true };
  }

  async pair(input: {
    code: string;
    nodeId: string;
    label: string;
    platform?: string;
    appVersion?: string;
  }) {
    const normalizedCode = input.code.trim().toUpperCase();
    const row = await this.prisma.founderNodePairingCode.findUnique({
      where: { code: normalizedCode },
    });

    if (!row || row.usedAt || row.expiresAt < new Date()) {
      throw new UnauthorizedException('Invalid or expired pairing code');
    }

    const nodeToken = `fn_${randomBytes(32).toString('hex')}`;
    const secretHash = await bcrypt.hash(nodeToken, 10);

    const node = await this.prisma.founderNode.upsert({
      where: { nodeId: input.nodeId },
      create: {
        userId: row.userId,
        nodeId: input.nodeId,
        label: input.label.trim() || 'Founder Node',
        secretHash,
        status: 'online',
        lastSeenAt: new Date(),
        platform: input.platform ?? null,
        appVersion: input.appVersion ?? null,
        vaultHealthy: true,
      },
      update: {
        userId: row.userId,
        label: input.label.trim() || 'Founder Node',
        secretHash,
        status: 'online',
        lastSeenAt: new Date(),
        platform: input.platform ?? null,
        appVersion: input.appVersion ?? null,
        vaultHealthy: true,
      },
    });

    await this.prisma.founderNodePairingCode.update({
      where: { id: row.id },
      data: { usedAt: new Date() },
    });

    await this.prisma.founderBuilderSettings.upsert({
      where: { userId: row.userId },
      create: { userId: row.userId, memoryStorageMode: 'FOUNDER_NODE' },
      update: { memoryStorageMode: 'FOUNDER_NODE' },
    });

    return {
      nodeToken,
      nodeId: node.nodeId,
      userId: row.userId,
    };
  }

  async validateNodeToken(nodeId: string, nodeToken: string) {
    const node = await this.prisma.founderNode.findUnique({ where: { nodeId } });
    if (!node) throw new UnauthorizedException('Unknown Founder Node');
    const ok = await bcrypt.compare(nodeToken, node.secretHash);
    if (!ok) throw new UnauthorizedException('Invalid Founder Node token');
    return node;
  }

  async heartbeat(nodeDbId: string, input: FounderNodeHeartbeat) {
    const node = await this.prisma.founderNode.update({
      where: { id: nodeDbId },
      data: {
        status: 'online',
        lastSeenAt: new Date(),
        label: input.label,
        ramGb: input.ramGb ?? null,
        storageGb: input.storageGb ?? null,
        storageFreeGb: input.storageFreeGb ?? null,
        vaultHealthy: input.vaultHealthy,
        platform: input.platform,
        appVersion: input.appVersion,
        ollamaEnabled: input.ollamaEnabled ?? false,
        ollamaBaseUrl: input.ollamaBaseUrl ?? null,
        ollamaModel: input.ollamaModel ?? null,
      },
    });
    void this.vaultSync.onNodeHeartbeat(node.userId, node.nodeId);
    return { success: true, status: 'online' as const };
  }

  async syncFromNode(userId: string, nodeDbId: string, payload: DeviceMemoryPayload) {
    const node = await this.prisma.founderNode.findUnique({ where: { id: nodeDbId } });
    if (!node) throw new NotFoundException('Node not found');

    await this.prisma.founderNode.update({
      where: { id: nodeDbId },
      data: {
        status: 'online',
        lastSeenAt: new Date(),
        vaultHealthy: true,
      },
    });

    const encryptedVaultBlob =
      payload && typeof payload === 'object' && 'encryptedVaultBlob' in payload
        ? String((payload as { encryptedVaultBlob?: string }).encryptedVaultBlob ?? '').trim()
        : '';

    if (encryptedVaultBlob.length > 0) {
      await this.prisma.founderNodeVaultRelay.upsert({
        where: { nodeId: node.nodeId },
        create: {
          userId,
          nodeId: node.nodeId,
          label: node.label,
          platform: node.platform,
          encryptedVaultBlob,
        },
        update: {
          label: node.label,
          platform: node.platform,
          encryptedVaultBlob,
        },
      });
    }

    const mergePatch =
      payload && typeof payload === 'object' && 'mergePatch' in payload
        ? (payload as { mergePatch?: VaultMergePatch }).mergePatch
        : undefined;
    if (mergePatch?.version === 1) {
      await this.vaultSync.recordRelayMerge(userId, node.nodeId, {
        label: node.label,
        platform: node.platform,
        mergePatch,
        fileManifest: mergePatch.fileManifest,
      });
    }

    return this.copilot.saveDeviceMemorySync(userId, {
      ...payload,
      deviceLabel: payload.deviceLabel ?? node.label ?? 'Founder Node',
    });
  }

  async listVaultRelays(userId: string) {
    const rows = await this.prisma.founderNodeVaultRelay.findMany({
      where: { userId },
      orderBy: { updatedAt: 'desc' },
    });
    return {
      relays: rows.map((r) => ({
        nodeId: r.nodeId,
        label: r.label,
        platform: r.platform,
        updatedAt: r.updatedAt.toISOString(),
        blobBytes: r.encryptedVaultBlob.length,
        vaultSyncVersion: r.vaultSyncVersion,
        hasMergePatch: r.mergePatch != null,
      })),
    };
  }

  async pullVaultRelayForNode(userId: string, nodeId: string) {
    const row = await this.prisma.founderNodeVaultRelay.findFirst({
      where: { userId, nodeId },
    });
    if (!row) throw new NotFoundException('No vault relay for this device');
    return {
      nodeId: row.nodeId,
      label: row.label,
      platform: row.platform,
      encryptedVaultBlob: row.encryptedVaultBlob,
      updatedAt: row.updatedAt.toISOString(),
    };
  }

  private generatePairingCode(): string {
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    let code = '';
    for (let i = 0; i < 8; i += 1) {
      code += alphabet[randomBytes(1)[0]! % alphabet.length];
    }
    return code;
  }

  private toStatusRow(n: {
    id: string;
    nodeId: string;
    label: string;
    status: string;
    lastSeenAt: Date | null;
    ramGb: number | null;
    storageGb: number | null;
    storageFreeGb: number | null;
    vaultHealthy: boolean;
    platform: string | null;
    appVersion: string | null;
  }) {
    const online =
      n.lastSeenAt != null && Date.now() - n.lastSeenAt.getTime() < ONLINE_WINDOW_MS;
    return {
      id: n.id,
      nodeId: n.nodeId,
      label: n.label,
      status: online ? ('online' as const) : ('offline' as const),
      lastSeenAt: n.lastSeenAt?.toISOString() ?? null,
      ramGb: n.ramGb,
      storageGb: n.storageGb,
      storageFreeGb: n.storageFreeGb,
      vaultHealthy: n.vaultHealthy,
      platform: n.platform,
      appVersion: n.appVersion,
    };
  }
}
