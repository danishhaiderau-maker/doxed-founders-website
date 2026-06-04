import { ForbiddenException, Injectable } from '@nestjs/common';
import { SecretsStorageMode } from '@prisma/client';
import {
  SecretsAccessDeniedError,
  assertUnwrapAllowed,
  readSecretsSeal,
  resolveSecretsTier,
  secretsTierForProvider,
  secretsTierLabel,
  secretsStorageModeLabel,
  withSecretsSeal,
  type SecretUnwrapPurpose,
  type SecretsSealTier,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { CredentialsCryptoService } from './credentials-crypto.service';

const SECRET_ACCESS_KIND = 'SECRET_ACCESS';

export type SecretsStatusDto = {
  mode: SecretsStorageMode;
  modeLabel: string;
  phalaInferenceOnly: boolean;
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

    return {
      mode,
      modeLabel: secretsStorageModeLabel(mode),
      phalaInferenceOnly,
      credentialCount: rows.length,
      sealedPhalaCount: rows.filter((r) => r.tier === 'phala_inference_only').length,
      credentials: rows,
      recentAccessCount: recentAccess,
      lastAccessAt: lastAccess?.createdAt.toISOString() ?? null,
      summary: phalaInferenceOnly
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
  ): Record<string, unknown> {
    return withSecretsSeal(metadata, secretsTierForProvider(provider));
  }

  async audit(
    userId: string,
    provider: string,
    action: 'store' | 'unwrap',
    purpose: SecretUnwrapPurpose,
  ): Promise<void> {
    await this.prisma.privacyAttestationLog
      .create({
        data: {
          userId,
          kind: SECRET_ACCESS_KIND,
          status: 'recorded',
          provider,
          summary: `${action}:${provider} (${purpose})`,
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
    const plain = this.crypto.decrypt(row.token);
    if (plain && !options?.skipAudit) {
      await this.audit(userId, provider, 'unwrap', purpose);
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
