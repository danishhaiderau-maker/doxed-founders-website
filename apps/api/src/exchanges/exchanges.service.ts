import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import { createHash, timingSafeEqual } from 'node:crypto';
import {
  EXCHANGE_PROVIDER_LABELS,
  exchangeCredentialProvider,
  type ExchangeProvider,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { CredentialCryptoService } from './credential-crypto.service';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import { ExchangeAdapterRegistry } from './exchange-adapter.registry';
import { BitfinexTradingClient } from './bitfinex-api.client';

export function bitfinexCredentialFingerprint(creds: ExchangeCredentials): string {
  return createHash('sha256')
    .update(`${creds.apiKey}\0${creds.apiSecret}`)
    .digest('hex');
}

export function credentialFingerprintMatches(actual: string, expected: string): boolean {
  if (!/^[a-f0-9]{64}$/i.test(actual) || !/^[a-f0-9]{64}$/i.test(expected)) return false;
  return timingSafeEqual(Buffer.from(actual.toLowerCase()), Buffer.from(expected.toLowerCase()));
}

export async function readBitfinexExchangeSnapshot(
  client: Pick<BitfinexTradingClient, 'listActiveOrders' | 'getOpenPositionDetail'>,
  creds: ExchangeCredentials,
) {
  try {
    const [orders, position] = await Promise.all([
      client.listActiveOrders(creds),
      client.getOpenPositionDetail(creds),
    ]);
    return { orders, position };
  } catch {
    // A partial or failed account read is unknown, never proof of a flat book.
    return null;
  }
}

@Injectable()
export class ExchangesService {
  private readonly registry = new ExchangeAdapterRegistry();
  private readonly bitfinex = new BitfinexTradingClient();

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
    const credentialFingerprint = provider === 'bitfinex'
      ? bitfinexCredentialFingerprint(creds)
      : undefined;

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
          ...(credentialFingerprint ? { accountCredentialFingerprint: credentialFingerprint } : {}),
        },
        verifiedAt: new Date(),
      },
      update: {
        token: payload,
        metadata: {
          exchange: provider,
          accountLabel: validation.accountLabel,
          permissions: validation.permissions,
          ...(credentialFingerprint ? { accountCredentialFingerprint: credentialFingerprint } : {}),
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

  async getUserAvailableUsd(userId: string, provider: string): Promise<number | null> {
    if (provider !== 'bitfinex') return null;
    const creds = await this.getUserCredentials(userId, provider);
    if (!creds) return null;
    try {
      return await this.bitfinex.getDerivativesAvailableUsd(creds);
    } catch {
      return null;
    }
  }

  async getUserBitfinexWalletSnapshot(userId: string) {
    const creds = await this.getUserCredentials(userId, 'bitfinex');
    if (!creds) return null;
    try {
      return await this.bitfinex.getWalletSnapshot(creds);
    } catch {
      return null;
    }
  }

  async getUserBitfinexLiveMetrics(
    userId: string,
    opts?: { sessionStartedAt?: Date; realizedPnlUsd?: number },
  ) {
    const creds = await this.getUserCredentials(userId, 'bitfinex');
    if (!creds) return null;
    try {
      return await this.bitfinex.getLiveAccountMetrics(creds, opts);
    } catch {
      return null;
    }
  }

  async getUserBitfinexExchangeSnapshot(userId: string) {
    const creds = await this.getUserCredentials(userId, 'bitfinex');
    if (!creds) return null;
    return readBitfinexExchangeSnapshot(this.bitfinex, creds);
  }

  /** Exchange-truth position closes for live-copy Trades table backfill when Neon ledger lags. */
  async getUserBitfinexPositionCloseLedger(userId: string, sessionStartedAt: Date) {
    const creds = await this.getUserCredentials(userId, 'bitfinex');
    if (!creds) return [];
    try {
      return await this.bitfinex.getPositionCloseLedgerEntries(creds, sessionStartedAt.getTime());
    } catch {
      return [];
    }
  }

  async ensureUserBitfinexDerivativesMargin(userId: string, minUsd: number) {
    const creds = await this.getUserCredentials(userId, 'bitfinex');
    if (!creds) return null;
    try {
      return await this.bitfinex.ensureDerivativesMargin(creds, minUsd);
    } catch {
      return null;
    }
  }

  async getUserCredentials(
    userId: string,
    provider: string,
  ): Promise<ExchangeCredentials | null> {
    const providerKey = exchangeCredentialProvider(provider as ExchangeProvider);
    const row = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: providerKey } },
    });
    if (!row?.token) return null;
    try {
      const parsed = JSON.parse(this.crypto.decrypt(row.token)) as ExchangeCredentials;
      if (!parsed.apiKey || !parsed.apiSecret) return null;
      if (providerKey === exchangeCredentialProvider('bitfinex')) {
        const actual = bitfinexCredentialFingerprint(parsed);
        const metadata = row.metadata && typeof row.metadata === 'object' && !Array.isArray(row.metadata)
          ? row.metadata as Record<string, unknown>
          : null;
        const storedExpected = typeof metadata?.accountCredentialFingerprint === 'string'
          ? metadata.accountCredentialFingerprint.trim()
          : '';
        const configuredExpected = process.env.BITFINEX_EXPECTED_CREDENTIAL_FINGERPRINT?.trim() ?? '';
        // Executor money paths must never silently switch accounts after a
        // partial credential rotation. The metadata fingerprint is written in
        // the same DB update as the encrypted token; an optional deployment
        // fingerprint pins the intended live key across database restores.
        if (
          (storedExpected && !credentialFingerprintMatches(actual, storedExpected))
          || (configuredExpected && !credentialFingerprintMatches(actual, configuredExpected))
          || (
            process.env.SUBSCRIBER_EXECUTION_ENABLED === 'true'
            && !storedExpected
            && !configuredExpected
          )
        ) return null;
      }
      return parsed;
    } catch {
      return null;
    }
  }
}
