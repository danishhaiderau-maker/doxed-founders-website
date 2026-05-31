import { BadRequestException, Injectable } from '@nestjs/common';
import { MemoryStorageMode, Prisma } from '@prisma/client';
import { extractVaultRelaySummary, type DeviceMemoryMetadataPayload } from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';
import { FounderNodeSyncService } from '../founder-node/founder-node-sync.service';
import {
  assessPhalaAttestationReport,
  DEFAULT_PHALA_INFERENCE_URL,
  DEFAULT_PHALA_MODEL,
  fetchPhalaAttestationReport,
  fetchPhalaRequestSignature,
  generatePhalaNonce,
  normalizePhalaBaseUrl,
  type PhalaChatResult,
  type PhalaCredentialMeta,
} from '../builder/phala.client';

@Injectable()
export class AttestationService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
    private readonly founderNodeSync: FounderNodeSyncService,
  ) {}

  async getDashboard(userId: string) {
    const [settings, deviceSync, nodeV2, recentPhala, recentVault] = await Promise.all([
      this.prisma.founderBuilderSettings.findUnique({ where: { userId } }),
      this.prisma.projectMemoryDeviceSync.findUnique({ where: { userId } }),
      this.founderNodeSync.getV2Status(userId),
      this.prisma.privacyAttestationLog.findMany({
        where: { userId, kind: 'PHALA_INFERENCE' },
        orderBy: { createdAt: 'desc' },
        take: 8,
      }),
      this.prisma.privacyAttestationLog.findFirst({
        where: { userId, kind: 'VAULT_INTEGRITY' },
        orderBy: { createdAt: 'desc' },
      }),
    ]);

    const memoryMode = settings?.memoryStorageMode ?? MemoryStorageMode.PLATFORM;
    const payload = deviceSync?.payload as { encryptedVaultBlob?: string } | null | undefined;
    const relay = extractVaultRelaySummary({
      memoryStorageMode: memoryMode,
      deviceSync: deviceSync
        ? {
            updatedAt: deviceSync.updatedAt.toISOString(),
            deviceLabel: deviceSync.deviceLabel,
            payload: deviceSync.payload as DeviceMemoryMetadataPayload,
          }
        : null,
    });

    const memoryChecks = [
      {
        name: 'memory_mode',
        ok: memoryMode === MemoryStorageMode.FOUNDER_NODE || memoryMode === MemoryStorageMode.LOCAL_SYNC,
        detail:
          memoryMode === MemoryStorageMode.FOUNDER_NODE
            ? 'Founder Vault (local-first)'
            : memoryMode === MemoryStorageMode.LOCAL_SYNC
              ? 'Local + encrypted relay'
              : 'Cloud memory — enable Founder Node for full self-custody',
      },
      {
        name: 'encrypted_relay',
        ok: Boolean(payload?.encryptedVaultBlob || relay?.hasEncryptedBlob),
        detail: relay?.hasEncryptedBlob
          ? 'Encrypted vault blob relayed — server cannot decrypt'
          : 'No encrypted relay yet',
      },
      {
        name: 'founder_node_online',
        ok: nodeV2.online,
        detail: nodeV2.online
          ? `${nodeV2.nodeLabel ?? 'Founder Node'} online`
          : 'Founder Node offline — open tray app',
      },
      {
        name: 'vector_index',
        ok: (nodeV2.vectorChunks ?? 0) > 0,
        detail:
          (nodeV2.vectorChunks ?? 0) > 0
            ? `${nodeV2.vectorChunks} local chunks indexed`
            : 'Rebuild vector index on Founder Node (Step 4)',
      },
    ];

    const memoryScore = Math.round(
      (memoryChecks.filter((c) => c.ok).length / memoryChecks.length) * 100,
    );

    const phalaVerified = recentPhala.filter((r) => r.verified).length;
    const phalaTotal = recentPhala.length;

    return {
      memoryIntegrity: {
        mode: memoryMode,
        score: memoryScore,
        status:
          memoryScore >= 75 ? ('healthy' as const) : memoryScore >= 40 ? ('partial' as const) : ('offline' as const),
        checks: memoryChecks,
        lastVaultScanAt: recentVault?.createdAt.toISOString() ?? null,
        relay,
        nodeV2,
      },
      phalaTee: {
        recentCount: phalaTotal,
        verifiedCount: phalaVerified,
        latest: recentPhala[0]
          ? {
              id: recentPhala[0].id,
              model: recentPhala[0].model,
              requestId: recentPhala[0].requestId,
              signingAddress: recentPhala[0].signingAddress,
              verified: recentPhala[0].verified,
              status: recentPhala[0].status,
              createdAt: recentPhala[0].createdAt.toISOString(),
              summary: recentPhala[0].summary,
            }
          : null,
        docsUrl: 'https://docs.redpill.ai/developers/api-reference/attestation',
      },
      recent: recentPhala.map((row) => ({
        id: row.id,
        kind: row.kind,
        model: row.model,
        requestId: row.requestId,
        verified: row.verified,
        status: row.status,
        summary: row.summary,
        createdAt: row.createdAt.toISOString(),
      })),
    };
  }

  async recordPhalaInference(userId: string, chat: PhalaChatResult, memoryMode?: MemoryStorageMode) {
    return this.prisma.privacyAttestationLog.create({
      data: {
        userId,
        kind: 'PHALA_INFERENCE',
        status: 'recorded',
        provider: 'phala',
        model: chat.model,
        requestId: chat.requestId,
        signingAddress: chat.signingAddress,
        summary: chat.requestId
          ? `Copilot inference ${chat.requestId.slice(0, 12)}…`
          : 'Copilot Phala inference (no request id)',
        memoryMode,
      },
    });
  }

  async scanVaultIntegrity(userId: string) {
    const dashboard = await this.getDashboard(userId);
    const checks = dashboard.memoryIntegrity.checks;
    const verified = checks.filter((c) => c.name !== 'memory_mode').every((c) => c.ok);

    return this.prisma.privacyAttestationLog.create({
      data: {
        userId,
        kind: 'VAULT_INTEGRITY',
        status: verified ? 'verified' : 'partial',
        provider: 'founder_node',
        verified,
        verifiedAt: verified ? new Date() : null,
        summary: `Vault integrity ${dashboard.memoryIntegrity.score}%`,
        checks: checks as unknown as Prisma.InputJsonValue,
        memoryMode: dashboard.memoryIntegrity.mode as MemoryStorageMode,
      },
    });
  }

  async verifyPhalaLog(userId: string, logId?: string) {
    const phala = await this.resolvePhalaCredentials(userId);
    if (!phala) {
      throw new BadRequestException('Connect Phala Private AI or enable platform credits first');
    }

    const log =
      logId != null
        ? await this.prisma.privacyAttestationLog.findFirst({
            where: { id: logId, userId, kind: 'PHALA_INFERENCE' },
          })
        : await this.prisma.privacyAttestationLog.findFirst({
            where: { userId, kind: 'PHALA_INFERENCE' },
            orderBy: { createdAt: 'desc' },
          });

    if (!log) throw new BadRequestException('No Phala inference to verify yet — ask Copilot with Phala as default');

    const model = log.model ?? phala.model;
    const nonce = generatePhalaNonce();
    let signingAddress = log.signingAddress;
    const report = await fetchPhalaAttestationReport({
      apiKey: phala.apiKey,
      inferenceUrl: phala.inferenceUrl,
      model,
      nonce,
      signingAddress,
    });

    let signature: Record<string, unknown> | null = null;
    if (log.requestId) {
      signature = await fetchPhalaRequestSignature({
        apiKey: phala.apiKey,
        inferenceUrl: phala.inferenceUrl,
        requestId: log.requestId,
        model,
      });
      const sigAddress =
        typeof signature?.signing_address === 'string' ? signature.signing_address : null;
      if (sigAddress && !signingAddress) signingAddress = sigAddress;
    }

    const assessment = assessPhalaAttestationReport(report, nonce);
    if (signature) {
      assessment.checks.push({
        name: 'response_signature',
        ok: Boolean(signature.signature || signature.text),
        detail: signature.signature ? 'Response signature fetched from Redpill' : 'Signature payload empty',
      });
    }

    const verified = assessment.checks.filter((c) => c.name !== 'gpu_attestation').every((c) => c.ok);

    return this.prisma.privacyAttestationLog.update({
      where: { id: log.id },
      data: {
        verified,
        verifiedAt: verified ? new Date() : null,
        status: verified ? 'verified' : 'failed',
        signingAddress: assessment.signingAddress ?? signingAddress,
        nonce,
        summary: verified
          ? `TEE attestation verified for ${model}`
          : `Attestation checks incomplete for ${model}`,
        checks: assessment.checks as unknown as Prisma.InputJsonValue,
        reportSnapshot: report as unknown as Prisma.InputJsonValue,
      },
    });
  }

  async fetchLiveAttestation(userId: string, model?: string, signingAddress?: string) {
    const phala = await this.resolvePhalaCredentials(userId);
    if (!phala) {
      throw new BadRequestException('Connect Phala Private AI or enable platform credits first');
    }

    const resolvedModel = model?.trim() || phala.model;
    const nonce = generatePhalaNonce();
    const report = await fetchPhalaAttestationReport({
      apiKey: phala.apiKey,
      inferenceUrl: phala.inferenceUrl,
      model: resolvedModel,
      nonce,
      signingAddress,
    });
    const assessment = assessPhalaAttestationReport(report, nonce);

    return {
      model: resolvedModel,
      nonce,
      verified: assessment.verified,
      checks: assessment.checks,
      signingAddress: assessment.signingAddress,
      report,
    };
  }

  private async resolvePhalaCredentials(userId: string) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'phala' } },
    });
    const userKey = this.crypto.decrypt(cred?.token);
    const meta = (cred?.metadata as PhalaCredentialMeta | null) ?? null;
    if (userKey) {
      return {
        apiKey: userKey,
        inferenceUrl: meta?.inferenceUrl || normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL),
        model: meta?.model || process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
      };
    }
    const platformKey = process.env.PHALA_API_KEY?.trim();
    if (!platformKey) return null;
    return {
      apiKey: platformKey,
      inferenceUrl: normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL || DEFAULT_PHALA_INFERENCE_URL),
      model: process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
    };
  }
}
