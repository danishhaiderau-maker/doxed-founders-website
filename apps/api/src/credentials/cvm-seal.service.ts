import { Injectable } from '@nestjs/common';
import { SecretsStorageMode } from '@prisma/client';
import {
  assessCvmSealReadiness,
  phalaCvmSealCapabilitiesPayload,
  readPhalaCvmSealPlatformConfig,
  resolveActiveUnwrapPath,
  resolvePreferredUnwrapPath,
  type PhalaCvmSealCapabilitiesPayload,
  type PhalaCvmSealStatusPayload,
  type SecretUnwrapPurpose,
  type SecretsUnwrapPath,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import {
  resolvePlatformCvmApiKey,
  resolvePlatformCvmUnwrapUrl,
  unwrapCredentialViaCvm,
} from './phala-cvm-unwrap.client';

@Injectable()
export class CvmSealService {
  constructor(private readonly prisma: PrismaService) {}

  getCapabilities(): PhalaCvmSealCapabilitiesPayload {
    return phalaCvmSealCapabilitiesPayload();
  }

  isPlatformCvmUnwrapConfigured(): boolean {
    return readPhalaCvmSealPlatformConfig().configured;
  }

  async getSealStatus(userId: string): Promise<PhalaCvmSealStatusPayload> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { secretsStorageMode: true },
    });
    const platform = readPhalaCvmSealPlatformConfig();
    const jwtSecretSet = Boolean(process.env.JWT_SECRET?.trim());
    const readiness = assessCvmSealReadiness({
      platformConfigured: platform.configured,
      jwtSecretSet,
    });
    const mode = settings?.secretsStorageMode ?? SecretsStorageMode.PLATFORM_ENCRYPTED;

    return {
      version: 1,
      cvmUnwrapReady: readiness.cvmUnwrapReady,
      platformUnwrapConfigured: platform.configured,
      preferredUnwrapPath: resolvePreferredUnwrapPath({
        platformConfigured: platform.configured,
        secretsStorageMode: mode,
      }),
      activeUnwrapPath: resolveActiveUnwrapPath(platform.configured),
      checks: readiness.checks,
      docsUrl: phalaCvmSealCapabilitiesPayload().docsUrl,
    };
  }

  async tryUnwrapViaCvm(input: {
    userId: string;
    provider: string;
    purpose: SecretUnwrapPurpose;
    encryptedToken: string;
  }): Promise<{ plain: string | null; path: SecretsUnwrapPath }> {
    const platform = readPhalaCvmSealPlatformConfig();
    if (!platform.configured) {
      return { plain: null, path: 'platform_encrypted' };
    }

    const unwrapUrl = resolvePlatformCvmUnwrapUrl();
    const apiKey = resolvePlatformCvmApiKey();
    if (!unwrapUrl || !apiKey) {
      return { plain: null, path: 'platform_encrypted' };
    }

    const result = await unwrapCredentialViaCvm({
      unwrapUrl,
      apiKey,
      payload: {
        encryptedToken: input.encryptedToken,
        provider: input.provider,
        purpose: input.purpose,
        userId: input.userId,
        workloadId: platform.workloadId,
      },
    });

    if (result.ok && result.plaintext) {
      return { plain: result.plaintext, path: 'cvm_sealed' };
    }

    return { plain: null, path: 'platform_encrypted' };
  }
}
