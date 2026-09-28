import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import { createHash, timingSafeEqual } from 'node:crypto';
import { Prisma } from '@prisma/client';
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

export type ExchangeCredentialResolutionCode =
  | 'OK'
  | 'ROW_MISSING'
  | 'TOKEN_MISSING'
  | 'DECRYPT_FAILED'
  | 'JSON_INVALID'
  | 'FIELDS_MISSING'
  | 'STORED_FINGERPRINT_MISMATCH'
  | 'CONFIGURED_FINGERPRINT_MISMATCH'
  | 'FINGERPRINT_REQUIRED_MISSING';

export type ExchangeCredentialResolution =
  | { ok: true; code: 'OK'; credentials: ExchangeCredentials }
  | { ok: false; code: Exclude<ExchangeCredentialResolutionCode, 'OK'>; credentials: null };

export type PreparedExchangeConnection = {
  provider: string;
  providerKey: string;
  encryptedToken: string;
  metadata: Prisma.InputJsonObject;
  message: string;
};

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
    const prepared = await this.prepareUserExchangeConnection(provider, creds);
    const row = await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: prepared.providerKey } },
      create: {
        userId,
        provider: prepared.providerKey,
        token: prepared.encryptedToken,
        metadata: prepared.metadata,
        verifiedAt: new Date(),
      },
      update: {
        token: prepared.encryptedToken,
        metadata: prepared.metadata,
        verifiedAt: new Date(),
      },
    });

    return {
      credentialId: row.id,
      provider,
      label: EXCHANGE_PROVIDER_LABELS[provider as ExchangeProvider],
      connected: true,
      message: prepared.message,
    };
  }

  /** Validate and encrypt a candidate without changing the credential store. */
  async prepareUserExchangeConnection(
    provider: string,
    creds: ExchangeCredentials,
  ): Promise<PreparedExchangeConnection> {
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
    const configuredExpected = process.env.BITFINEX_EXPECTED_CREDENTIAL_FINGERPRINT?.trim() ?? '';
    if (
      credentialFingerprint
      && configuredExpected
      && !credentialFingerprintMatches(credentialFingerprint, configuredExpected)
    ) {
      throw new BadRequestException(
        'Bitfinex credentials do not match the configured account identity',
      );
    }

    return {
      provider,
      providerKey,
      encryptedToken: payload,
      metadata: {
        exchange: provider,
        accountLabel: validation.accountLabel,
        permissions: validation.permissions,
        ...(credentialFingerprint ? { accountCredentialFingerprint: credentialFingerprint } : {}),
      },
      message: validation.message,
    };
  }

  /** Authenticated candidate-only read; never consults or updates stored credentials. */
  async readBitfinexCandidateSnapshot(creds: ExchangeCredentials) {
    return readBitfinexExchangeSnapshot(this.bitfinex, creds);
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
    const resolution = await this.resolveUserCredentials(userId, provider);
    return resolution.credentials;
  }

  /**
   * Resolve exchange credentials while retaining a non-secret failure class.
   * Callers must never serialize the successful result because it contains the
   * decrypted key material; only the `code` is safe for logs/dashboard state.
   */
  async resolveUserCredentials(
    userId: string,
    provider: string,
  ): Promise<ExchangeCredentialResolution> {
    const providerKey = exchangeCredentialProvider(provider as ExchangeProvider);
    const row = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: providerKey } },
    });
    if (!row) return { ok: false, code: 'ROW_MISSING', credentials: null };
    if (!row.token?.trim()) return { ok: false, code: 'TOKEN_MISSING', credentials: null };

    let decrypted: string;
    try {
      decrypted = this.crypto.decrypt(row.token);
    } catch {
      return { ok: false, code: 'DECRYPT_FAILED', credentials: null };
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(decrypted);
    } catch {
      return { ok: false, code: 'JSON_INVALID', credentials: null };
    }
    if (
      parsed == null
      || typeof parsed !== 'object'
      || Array.isArray(parsed)
    ) {
      return { ok: false, code: 'FIELDS_MISSING', credentials: null };
    }
    const parsedRecord = parsed as Record<string, unknown>;
    const apiKey = parsedRecord.apiKey;
    const apiSecret = parsedRecord.apiSecret;
    if (
      typeof apiKey !== 'string'
      || !apiKey.trim()
      || typeof apiSecret !== 'string'
      || !apiSecret.trim()
    ) {
      return { ok: false, code: 'FIELDS_MISSING', credentials: null };
    }

    const credentials = parsed as ExchangeCredentials;
    if (providerKey === exchangeCredentialProvider('bitfinex')) {
      const actual = bitfinexCredentialFingerprint(credentials);
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
      if (storedExpected && !credentialFingerprintMatches(actual, storedExpected)) {
        return { ok: false, code: 'STORED_FINGERPRINT_MISMATCH', credentials: null };
      }
      if (configuredExpected && !credentialFingerprintMatches(actual, configuredExpected)) {
        return { ok: false, code: 'CONFIGURED_FINGERPRINT_MISMATCH', credentials: null };
      }
      if (
        process.env.SUBSCRIBER_EXECUTION_ENABLED === 'true'
        && !storedExpected
        && !configuredExpected
      ) {
        return { ok: false, code: 'FINGERPRINT_REQUIRED_MISSING', credentials: null };
      }
    }
    return { ok: true, code: 'OK', credentials };
  }
}
