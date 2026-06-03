import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import {
  EXCHANGE_PROVIDER_LABELS,
  exchangeCredentialProvider,
  type ExchangeProvider,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { CredentialCryptoService } from './credential-crypto.service';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import { ExchangeAdapterRegistry } from './exchange-adapter.registry';

@Injectable()
export class ExchangesService {
  private readonly registry = new ExchangeAdapterRegistry();

  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialCryptoService,
  ) {}

  listProviders() {
    return this.registry.listProviders().map((id) => ({
      id,
      label: EXCHANGE_PROVIDER_LABELS[id],
      available: this.registry.isAvailable(id),
    }));
  }

  async connectUserExchange(
    userId: string,
    provider: string,
    creds: ExchangeCredentials,
  ) {
    const adapter = this.registry.get(provider);
    if (!adapter) throw new BadRequestException(`Unsupported exchange: ${provider}`);

    const validation = await adapter.validateCredentials(creds);
    if (!validation.ok) {
      throw new BadRequestException(validation.message);
    }

    const providerKey = exchangeCredentialProvider(provider as ExchangeProvider);
    const payload = this.crypto.encrypt(
      JSON.stringify({
        apiKey: creds.apiKey,
        apiSecret: creds.apiSecret,
        passphrase: creds.passphrase,
        testnet: creds.testnet ?? false,
      }),
    );

    const row = await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: providerKey } },
      create: {
        userId,
        provider: providerKey,
        token: payload,
        metadata: {
          exchange: provider,
          accountLabel: validation.accountLabel,
          permissions: validation.permissions,
        },
        verifiedAt: new Date(),
      },
      update: {
        token: payload,
        metadata: {
          exchange: provider,
          accountLabel: validation.accountLabel,
          permissions: validation.permissions,
        },
        verifiedAt: new Date(),
      },
    });

    return {
      credentialId: row.id,
      provider,
      label: EXCHANGE_PROVIDER_LABELS[provider as ExchangeProvider],
      connected: true,
      message: validation.message,
    };
  }

  async getUserExchangeStatus(userId: string, provider: string) {
    const providerKey = exchangeCredentialProvider(provider as ExchangeProvider);
    const row = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: providerKey } },
    });
    if (!row) return { connected: false, provider };
    const meta = row.metadata as { accountLabel?: string } | null;
    return {
      connected: true,
      provider,
      credentialId: row.id,
      accountLabel: meta?.accountLabel ?? null,
      verifiedAt: row.verifiedAt?.toISOString() ?? null,
    };
  }

  async disconnectUserExchange(userId: string, provider: string) {
    const providerKey = exchangeCredentialProvider(provider as ExchangeProvider);
    await this.prisma.integrationCredential.deleteMany({
      where: { userId, provider: providerKey },
    });
    return { disconnected: true };
  }
}
