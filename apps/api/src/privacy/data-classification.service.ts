import { Injectable } from '@nestjs/common';
import { FounderNodeSyncJobStatus, Prisma } from '@prisma/client';
import {
  API_ROUTE_CLASSIFICATION,
  DATA_CLASS_CATALOG,
  PRISMA_MODEL_CLASSIFICATION,
  runStaticDataClassAudit,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { SealedCredentialsService } from '../credentials/sealed-credentials.service';

@Injectable()
export class DataClassificationService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly sealed: SealedCredentialsService,
  ) {}

  getOverview() {
    const audit = runStaticDataClassAudit();
    return {
      version: 1,
      hybridModel: {
        publicLayer: 'Neon Postgres — product, feed, discover, paper trading',
        privateLayer:
          'Founder Node vault + encrypted credentials + memory graph (owner-scoped JWT routes)',
        teeLayer: 'Phala confidential inference (optional) — see attestation dashboard',
      },
      classes: DATA_CLASS_CATALOG,
      prismaModels: PRISMA_MODEL_CLASSIFICATION,
      apiRoutes: API_ROUTE_CLASSIFICATION,
      audit,
      nextPhase: 'Phala P2 — CVM-side credential unwrap',
    };
  }

  async getRuntimeAudit() {
    const staticAudit = runStaticDataClassAudit();
    const [credentialRows, githubRows, memoryGraphRows, syncJobRows] = await Promise.all([
      this.prisma.integrationCredential.count({ where: { token: { not: null } } }),
      this.prisma.gitHubConnection.count({ where: { accessTokenEncrypted: { not: null } } }),
      this.prisma.founderBuilderSettings.count({
        where: { memoryGraph: { not: Prisma.DbNull } },
      }),
      this.prisma.founderNodeSyncJob.count({
        where: {
          status: { in: [FounderNodeSyncJobStatus.PENDING, FounderNodeSyncJobStatus.PROCESSING] },
        },
      }),
    ]);

    return {
      ...staticAudit,
      runtime: {
        encryptedIntegrationCredentials: credentialRows,
        encryptedGitHubConnections: githubRows,
        foundersWithMemoryGraph: memoryGraphRows,
        pendingFounderNodeSyncJobs: syncJobRows,
        checkedAt: new Date().toISOString(),
      },
      policy: {
        publicApiNeverReturns: ['token', 'accessTokenEncrypted', 'webhookSecret', 'secretHash'],
        unwrapPath: 'SealedCredentialsService only — audited in privacyAttestationLog',
      },
    };
  }

  async getMyBoundaries(userId: string) {
    const [secretsStatus, settings, node] = await Promise.all([
      this.sealed.getStatus(userId),
      this.prisma.founderBuilderSettings.findUnique({
        where: { userId },
        select: { memoryStorageMode: true, secretsStorageMode: true },
      }),
      this.prisma.founderNode.findFirst({
        where: { userId },
        orderBy: { lastSeenAt: 'desc' },
        select: { nodeId: true, label: true, status: true, vaultHealthy: true, lastSeenAt: true },
      }),
    ]);

    return {
      yourData: {
        memory: {
          mode: settings?.memoryStorageMode ?? 'PLATFORM',
          class: 'founder_private' as const,
          note: 'Memory graph and copilot context are only returned to your signed-in session.',
        },
        secrets: {
          mode: secretsStatus.mode,
          modeLabel: secretsStatus.modeLabel,
          class: 'sealed_credential' as const,
          credentialCount: secretsStatus.credentialCount,
          phalaInferenceOnly: secretsStatus.phalaInferenceOnly,
          note: secretsStatus.summary,
        },
        founderNode: node
          ? {
              class: 'founder_node_relay' as const,
              nodeId: node.nodeId,
              label: node.label,
              status: node.status,
              vaultHealthy: node.vaultHealthy,
              lastSeenAt: node.lastSeenAt?.toISOString() ?? null,
            }
          : null,
      },
      publicProduct: {
        class: 'public_product' as const,
        note: 'Approved project listings, feed, and discover metrics never include your API keys.',
      },
    };
  }
}
