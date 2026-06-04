import { createHash } from 'node:crypto';
import { BadRequestException, Injectable } from '@nestjs/common';
import { MemoryStorageMode } from '@prisma/client';
import {
  PHALA_CVM_VAULT_ATTESTATION_KIND,
  assessCvmVaultReadiness,
  extractVaultRelaySummary,
  phalaCvmVaultDocsUrl,
  readPhalaCvmPlatformConfig,
  resolveCvmBackupState,
  type PhalaCvmCapabilitiesPayload,
  type PhalaCvmVaultStatusPayload,
  type DeviceMemoryMetadataPayload,
} from '@dcf/utils';
import {
  assessPhalaAttestationReport,
  fetchPhalaAttestationReport,
  generatePhalaNonce,
  DEFAULT_PHALA_MODEL,
  normalizePhalaBaseUrl,
  type PhalaCredentialMeta,
} from '../builder/phala.client';
import { SealedCredentialsService } from '../credentials/sealed-credentials.service';
import { FounderNodeSyncService } from '../founder-node/founder-node-sync.service';
import { PrismaService } from '../prisma/prisma.service';
import {
  pushVaultBackupToCvm,
  resolvePlatformCvmApiKey,
  resolvePlatformCvmBackupUrl,
} from './phala-cvm.client';

@Injectable()
export class VaultCvmService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly sealed: SealedCredentialsService,
    private readonly founderNodeSync: FounderNodeSyncService,
  ) {}

  getCapabilities(): PhalaCvmCapabilitiesPayload {
    const platform = readPhalaCvmPlatformConfig();
    return {
      version: 1,
      platformCvmConfigured: platform.configured,
      backupUrlSet: platform.backupUrlSet,
      workloadId: platform.workloadId,
      docsUrl: phalaCvmVaultDocsUrl(),
    };
  }

  async getStatus(userId: string): Promise<PhalaCvmVaultStatusPayload> {
    const [settings, deviceSync, nodeV2, lastBackup, phalaCred] = await Promise.all([
      this.prisma.founderBuilderSettings.findUnique({ where: { userId } }),
      this.prisma.projectMemoryDeviceSync.findUnique({ where: { userId } }),
      this.founderNodeSync.getV2Status(userId),
      this.prisma.privacyAttestationLog.findFirst({
        where: { userId, kind: PHALA_CVM_VAULT_ATTESTATION_KIND },
        orderBy: { createdAt: 'desc' },
      }),
      this.prisma.integrationCredential.findUnique({
        where: { userId_provider: { userId, provider: 'phala' } },
      }),
    ]);

    const memoryMode = settings?.memoryStorageMode ?? MemoryStorageMode.PLATFORM;
    const payload = deviceSync?.payload as { encryptedVaultBlob?: string } | null | undefined;
    const blob = payload?.encryptedVaultBlob;
    const blobHash = blob ? createHash('sha256').update(blob).digest('hex') : null;

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

    const platform = readPhalaCvmPlatformConfig();
    const platformKey = resolvePlatformCvmApiKey();
    const userPhalaConnected = Boolean(phalaCred?.verifiedAt) || Boolean(platformKey);

    const readiness = assessCvmVaultReadiness({
      platformConfigured: platform.configured,
      userPhalaConnected,
      hasEncryptedBlob: Boolean(blob || relay?.hasEncryptedBlob),
      founderNodeOnline: nodeV2.online,
      memoryModeFounderVault:
        memoryMode === MemoryStorageMode.FOUNDER_NODE ||
        memoryMode === MemoryStorageMode.LOCAL_SYNC,
    });

    const backupState = resolveCvmBackupState({
      platformConfigured: platform.configured,
      lastLog: lastBackup,
      pendingRequest: false,
    });

    return {
      version: 1,
      mode: readiness.mode,
      platformCvmAvailable: platform.configured,
      userPhalaConnected,
      backupState,
      canRequestBackup: readiness.canRequestBackup,
      checks: readiness.checks,
      relay: {
        hasEncryptedBlob: Boolean(blob || relay?.hasEncryptedBlob),
        lastSyncedAt: relay?.lastSyncedAt ?? deviceSync?.updatedAt.toISOString() ?? null,
        deviceLabel: relay?.deviceLabel ?? deviceSync?.deviceLabel ?? null,
        blobHashPrefix: blobHash ? blobHash.slice(0, 16) : null,
      },
      lastBackup: lastBackup
        ? {
            id: lastBackup.id,
            status: lastBackup.status,
            verified: lastBackup.verified,
            summary: lastBackup.summary,
            signingAddress: lastBackup.signingAddress,
            createdAt: lastBackup.createdAt.toISOString(),
          }
        : null,
      docsUrl: phalaCvmVaultDocsUrl(),
    };
  }

  async requestBackup(userId: string) {
    const status = await this.getStatus(userId);
    if (!status.canRequestBackup) {
      throw new BadRequestException(
        'Complete vault relay sync and connect Phala before requesting CVM backup',
      );
    }

    const deviceSync = await this.prisma.projectMemoryDeviceSync.findUnique({ where: { userId } });
    const payload = deviceSync?.payload as {
      encryptedVaultBlob?: string;
      tasksRemaining?: number;
      currentGoal?: string;
    } | null;
    const blob = payload?.encryptedVaultBlob;
    if (!blob) {
      throw new BadRequestException('No encrypted vault blob on relay yet');
    }

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const memoryMode = settings?.memoryStorageMode ?? MemoryStorageMode.PLATFORM;
    const blobHash = createHash('sha256').update(blob).digest('hex');
    const platform = readPhalaCvmPlatformConfig();
    const backupUrl = resolvePlatformCvmBackupUrl();
    const apiKey = resolvePlatformCvmApiKey();

    let cvmResult: Awaited<ReturnType<typeof pushVaultBackupToCvm>> | null = null;
    if (platform.configured && backupUrl && apiKey) {
      cvmResult = await pushVaultBackupToCvm({
        backupUrl,
        apiKey,
        payload: {
          blobHash,
          relayUpdatedAt: deviceSync!.updatedAt.toISOString(),
          memoryMode,
          deviceLabel: deviceSync!.deviceLabel,
          taskCount: payload?.tasksRemaining ?? 0,
          workloadId: platform.workloadId,
        },
      });
    }

    const verified = Boolean(cvmResult?.ok);
    const summary = cvmResult?.ok
      ? `CVM vault backup recorded (${cvmResult.backupId ?? blobHash.slice(0, 12)}…)`
      : platform.configured
        ? `Local relay sealed — CVM push failed: ${cvmResult?.error ?? 'unknown'}`
        : 'Local relay backup snapshot — enable PHALA_CVM_BACKUP_URL for TEE workload';

    const log = await this.prisma.privacyAttestationLog.create({
      data: {
        userId,
        kind: PHALA_CVM_VAULT_ATTESTATION_KIND,
        status: verified ? 'recorded' : platform.configured ? 'failed' : 'local_relay',
        provider: 'phala_cvm',
        requestId: cvmResult?.backupId ?? blobHash.slice(0, 32),
        signingAddress: cvmResult?.signingAddress,
        verified: false,
        summary,
        memoryMode: memoryMode as MemoryStorageMode,
        checks: status.checks as unknown as import('@prisma/client').Prisma.InputJsonValue,
        reportSnapshot: (cvmResult?.receipt ?? { blobHashPrefix: blobHash.slice(0, 16) }) as import('@prisma/client').Prisma.InputJsonValue,
      },
    });

    return {
      logId: log.id,
      status: log.status,
      verified: log.verified,
      summary: log.summary,
      mode: status.mode,
      platformCvmPushed: Boolean(cvmResult?.ok),
      cvmError: cvmResult?.error ?? null,
    };
  }

  async verifyBackup(userId: string, logId?: string) {
    const phala = await this.resolvePhalaCredentials(userId);
    if (!phala) {
      throw new BadRequestException('Connect Phala Private AI or enable platform credits first');
    }

    const log =
      logId != null
        ? await this.prisma.privacyAttestationLog.findFirst({
            where: { id: logId, userId, kind: PHALA_CVM_VAULT_ATTESTATION_KIND },
          })
        : await this.prisma.privacyAttestationLog.findFirst({
            where: { userId, kind: PHALA_CVM_VAULT_ATTESTATION_KIND },
            orderBy: { createdAt: 'desc' },
          });

    if (!log) {
      throw new BadRequestException('No CVM vault backup to verify — request a backup first');
    }

    const model = phala.model;
    const nonce = generatePhalaNonce();
    const signingAddress = log.signingAddress;
    const report = await fetchPhalaAttestationReport({
      apiKey: phala.apiKey,
      inferenceUrl: phala.inferenceUrl,
      model,
      nonce,
      signingAddress,
    });
    const assessment = assessPhalaAttestationReport(report, nonce);
    const verified = assessment.checks.filter((c) => c.name !== 'gpu_attestation').every((c) => c.ok);

    return this.prisma.privacyAttestationLog.update({
      where: { id: log.id },
      data: {
        verified,
        verifiedAt: verified ? new Date() : null,
        status: verified ? 'verified' : 'failed',
        nonce,
        signingAddress: assessment.signingAddress ?? signingAddress,
        summary: verified
          ? `CVM vault backup TEE verified (${model})`
          : `CVM backup attestation incomplete (${model})`,
        checks: assessment.checks as unknown as import('@prisma/client').Prisma.InputJsonValue,
        reportSnapshot: report as unknown as import('@prisma/client').Prisma.InputJsonValue,
      },
    });
  }

  private async resolvePhalaCredentials(userId: string) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'phala' } },
    });
    const userKey = await this.sealed.unwrapPhala(userId, 'phala_attestation');
    const meta = (cred?.metadata as PhalaCredentialMeta | null) ?? null;
    if (userKey) {
      return {
        apiKey: userKey,
        inferenceUrl: meta?.inferenceUrl || normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL),
        model: meta?.model || process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
      };
    }
    const platformKey = resolvePlatformCvmApiKey();
    if (!platformKey) return null;
    return {
      apiKey: platformKey,
      inferenceUrl: normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL),
      model: process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
    };
  }
}
