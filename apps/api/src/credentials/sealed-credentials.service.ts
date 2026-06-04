import { ForbiddenException, Injectable } from '@nestjs/common';
import { SecretsStorageMode } from '@prisma/client';
import {
  SecretsAccessDeniedError,
  assertUnwrapAllowed,
  readPhalaCvmSealPlatformConfig,
  readSecretsSeal,
  resolveSecretsTier,
  secretsTierForProvider,
  secretsTierForStorageMode,
  secretsTierLabel,
  secretsStorageModeLabel,
  unwrapPathLabel,
  withSecretsSeal,
  type SecretUnwrapPurpose,
  type SecretsSealTier,
  type SecretsUnwrapPath,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { CredentialsCryptoService } from './credentials-crypto.service';
import { CvmSealService } from './cvm-seal.service';

const SECRET_ACCESS_KIND = 'SECRET_ACCESS';

export type SecretsStatusDto = {
  mode: SecretsStorageMode;
  modeLabel: string;
  phalaInferenceOnly: boolean;
  cvmUnwrapReady: boolean;
  activeUnwrapPath: SecretsUnwrapPath;
  activeUnwrapPathLabel: string;
  credentialCount: number;
  sealedPhalaCount: number;
  credentials: Array<{
    provider: string;
    tier: SecretsSealTier;
    tierLabel: string;
    connected: boolean;
    sealedAt: string | null;
  }>;
  recentAccessCount: number;
  lastAccessAt: string | null;
  summary: string;
};

@Injectable()
export class SealedCredentialsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
    private readonly cvmSeal: CvmSealService,
  ) {}

  async getStatus(userId: string): Promise<SecretsStatusDto> {
    const [settings, creds, recentAccess, lastAccess] = await Promise.all([
      this.prisma.founderBuilderSettings.findUnique({ where: { userId } }),
      this.prisma.integrationCredential.findMany({
        where: { userId },
        select: { provider: true, token: true, metadata: true, verifiedAt: true },
      }),
      this.prisma.privacyAttestationLog.count({
        where: {
          userId,
          kind: SECRET_ACCESS_KIND,
          createdAt: { gte: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000) },
        },
      }),
      this.prisma.privacyAttestationLog.findFirst({
        where: { userId, kind: SECRET_ACCESS_KIND },
        orderBy: { createdAt: 'desc' },
        select: { createdAt: true },
      }),
    ]);

    const mode = settings?.secretsStorageMode ?? SecretsStorageMode.PLATFORM_ENCRYPTED;
    const rows = creds
      .filter((c) => Boolean(c.token?.trim()))
      .map((c) => {
        const tier = resolveSecretsTier(c.provider, c.metadata);
        const seal = readSecretsSeal(c.metadata);
        return {
          provider: c.provider,
          tier,
          tierLabel: secretsTierLabel(tier),
          connected: Boolean(c.verifiedAt),
          sealedAt: seal?.storedAt ?? null,
        };
      });

    const phalaInferenceOnly = rows.some(
      (r) => r.provider === 'phala' && r.tier === 'phala_inference_only',
    );
    const sealStatus = await this.cvmSeal.getSealStatus(userId);
    const cvmConfigured = readPhalaCvmSealPlatformConfig().configured;
    const activeUnwrapPath = sealStatus.activeUnwrapPath;

    return {
      mode,
      modeLabel: secretsStorageModeLabel(mode),
      phalaInferenceOnly,
      cvmUnwrapReady: sealStatus.cvmUnwrapReady,
      activeUnwrapPath,
      activeUnwrapPathLabel: unwrapPathLabel(activeUnwrapPath),
      credentialCount: rows.length,
      sealedPhalaCount: rows.filter((r) => r.tier === 'phala_inference_only').length,
      credentials: rows,
      recentAccessCount: recentAccess,
      lastAccessAt: lastAccess?.createdAt.toISOString() ?? null,
      summary: cvmConfigured
        ? 'Credential unwrap routes through Phala CVM when configured; falls back to platform AES if the workload is unreachable.'
        : phalaInferenceOnly
          ? 'Phala keys are inference-sealed; other keys are AES-encrypted and only unwrapped on the server for approved tasks.'
          : 'API keys are AES-256-GCM encrypted at rest; unwrap events are audited — raw keys never reach the browser.',
    };
  }

  async hasCredential(userId: string, provider: string): Promise<boolean> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider } },
      select: { token: true },
    });
    return Boolean(cred?.token?.trim());
  }

  encryptForStore(plain: string): string {
    return this.crypto.encrypt(plain);
  }

  sealMetadata(
    provider: string,
    metadata: Record<string, unknown> | null | undefined,
    secretsStorageMode?: SecretsStorageMode,
  ): Record<string, unknown> {
    const tier = secretsStorageMode
      ? secretsTierForStorageMode(provider, secretsStorageMode)
      : secretsTierForProvider(provider);
    return withSecretsSeal(metadata, tier);
  }

  async audit(
    userId: string,
    provider: string,
    action: 'store' | 'unwrap',
    purpose: SecretUnwrapPurpose,
    unwrapPath?: SecretsUnwrapPath,
  ): Promise<void> {
    const pathSuffix = unwrapPath ? ` [${unwrapPath}]` : '';
    await this.prisma.privacyAttestationLog
      .create({
        data: {
          userId,
          kind: SECRET_ACCESS_KIND,
          status: 'recorded',
          provider,
          summary: `${action}:${provider} (${purpose})${pathSuffix}`,
        },
      })
      .catch(() => undefined);
  }

  async unwrap(
    userId: string,
    provider: string,
    purpose: SecretUnwrapPurpose,
    options?: { skipAudit?: boolean },
  ): Promise<string | null> {
    const row = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider } },
    });
    if (!row?.token?.trim()) return null;
    const tier = resolveSecretsTier(provider, row.metadata);
    try {
      assertUnwrapAllowed(tier, purpose);
    } catch (e) {
      if (e instanceof SecretsAccessDeniedError) {
        throw new ForbiddenException(e.message);
      }
      throw e;
    }
    let plain: string | null = null;
    let unwrapPath: SecretsUnwrapPath = 'platform_encrypted';

    const cvmAttempt = await this.cvmSeal.tryUnwrapViaCvm({
      userId,
      provider,
      purpose,
      encryptedToken: row.token,
    });
    if (cvmAttempt.plain) {
      plain = cvmAttempt.plain;
      unwrapPath = cvmAttempt.path;
    } else {
      plain = this.crypto.decrypt(row.token);
      unwrapPath = 'platform_encrypted';
    }

    if (plain && !options?.skipAudit) {
      await this.audit(userId, provider, 'unwrap', purpose, unwrapPath);
    }
    return plain;
  }

  async unwrapPhala(
    userId: string,
    purpose: 'phala_inference' | 'phala_attestation' | 'connect_verify' | 'status_probe',
  ): Promise<string | null> {
    return this.unwrap(userId, 'phala', purpose);
  }
}
