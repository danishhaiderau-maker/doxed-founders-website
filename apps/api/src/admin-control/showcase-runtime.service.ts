import { BadRequestException, Injectable } from '@nestjs/common';
import {
  EXCHANGE_PROVIDER_LABELS,
  TRADING_AGENT_AI_PROVIDER_LABELS,
  exchangeBotRuntimeNote,
  exchangeRequiresPassphrase,
  type ExchangeProvider,
  type TradingAgentAiProvider,
} from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';
import { ExchangeAdapterRegistry } from '../exchanges/exchange-adapter.registry';
import type { ExchangeCredentials } from '../exchanges/exchange-adapter.interface';
import {
  CANONICAL_SHOWCASE_BOT_URL,
  normalizeShowcaseBotUrl,
} from '../trading-agents/canonical-showcase-runtime';

type ShowcaseExchangePayload = {
  apiKey: string;
  apiSecret: string;
  passphrase?: string;
  testnet?: boolean;
};

@Injectable()
export class ShowcaseRuntimeService {
  private readonly registry = new ExchangeAdapterRegistry();

  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
  ) {}

  async getCredentialsStatus() {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const exchangeProvider = 'bitfinex' as ExchangeProvider;
    const aiProvider = (row?.showcaseAiProvider ?? 'deepseek') as TradingAgentAiProvider;

    return {
      exchangeProvider,
      exchangeLabel: EXCHANGE_PROVIDER_LABELS[exchangeProvider] ?? exchangeProvider,
      aiProvider,
      aiLabel: TRADING_AGENT_AI_PROVIDER_LABELS[aiProvider] ?? aiProvider,
      exchangeConfigured: Boolean(row?.showcaseExchangeCredentialEnc),
      aiConfigured: Boolean(row?.showcaseAiCredentialEnc),
      botPublicUrl: CANONICAL_SHOWCASE_BOT_URL,
      credentialsUpdatedAt: row?.showcaseCredentialsUpdatedAt?.toISOString() ?? null,
      runtimePushedAt: row?.showcaseRuntimePushedAt?.toISOString() ?? null,
      runtimeManagedBy: 'fly_deployment_secrets',
      runtimePushRetired: true,
      botRuntimeNote: exchangeBotRuntimeNote(exchangeProvider),
      aiRuntimeNote:
        aiProvider !== 'deepseek'
          ? 'The canonical bot uses the AI provider secret configured on Fly.io.'
          : null,
    };
  }

  async saveShowcaseCredentials(
    userId: string,
    input: {
      exchangeProvider?: string;
      aiProvider?: string;
      exchangeApiKey?: string;
      exchangeApiSecret?: string;
      exchangePassphrase?: string;
      testnet?: boolean;
      aiApiKey?: string;
      botPublicUrl?: string;
    },
  ) {
    if (
      input.botPublicUrl?.trim() &&
      normalizeShowcaseBotUrl(input.botPublicUrl) !== CANONICAL_SHOWCASE_BOT_URL
    ) {
      throw new BadRequestException(
        `The Conservative BTC runtime is locked to ${CANONICAL_SHOWCASE_BOT_URL}`,
      );
    }
    if (input.exchangeProvider && input.exchangeProvider !== 'bitfinex') {
      throw new BadRequestException(
        'Conservative BTC is locked to Bitfinex; another showcase exchange cannot be selected.',
      );
    }

    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const exchangeProvider = 'bitfinex' as ExchangeProvider;
    const aiProvider = (input.aiProvider ??
      row?.showcaseAiProvider ??
      'deepseek') as TradingAgentAiProvider;

    let showcaseExchangeCredentialEnc = row?.showcaseExchangeCredentialEnc ?? null;
    let showcaseAiCredentialEnc = row?.showcaseAiCredentialEnc ?? null;

    const key = input.exchangeApiKey?.trim();
    const secret = input.exchangeApiSecret?.trim();
    if (key || secret) {
      if (!key || !secret) {
        throw new BadRequestException('Both exchange API key and secret are required to update');
      }
      if (exchangeRequiresPassphrase(exchangeProvider) && !input.exchangePassphrase?.trim()) {
        throw new BadRequestException('Passphrase is required for this exchange');
      }
      const adapter = this.registry.get(exchangeProvider);
      if (!adapter) {
        throw new BadRequestException(`Exchange ${exchangeProvider} is not supported yet`);
      }
      const creds: ExchangeCredentials = {
        apiKey: key,
        apiSecret: secret,
        passphrase: input.exchangePassphrase?.trim(),
        testnet: input.testnet ?? false,
      };
      const validation = await adapter.validateCredentials(creds);
      if (!validation.ok) {
        throw new BadRequestException(validation.message ?? 'Exchange credentials invalid');
      }
      showcaseExchangeCredentialEnc = this.crypto.encrypt(
        JSON.stringify({
          apiKey: key,
          apiSecret: secret,
          passphrase: creds.passphrase,
          testnet: creds.testnet,
        } satisfies ShowcaseExchangePayload),
      );
    }

    const aiKey = input.aiApiKey?.trim();
    if (aiKey) {
      if (aiKey.length < 8) throw new BadRequestException('AI API key too short');
      showcaseAiCredentialEnc = this.crypto.encrypt(aiKey);
    }

    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        showcaseExchangeProvider: exchangeProvider,
        showcaseAiProvider: aiProvider,
        showcaseExchangeCredentialEnc,
        showcaseAiCredentialEnc,
        showcaseBotPublicUrl: CANONICAL_SHOWCASE_BOT_URL,
        showcaseCredentialsUpdatedAt: new Date(),
        updatedByUserId: userId,
      },
      update: {
        showcaseExchangeProvider: exchangeProvider,
        ...(input.aiProvider ? { showcaseAiProvider: aiProvider } : {}),
        ...(showcaseExchangeCredentialEnc ? { showcaseExchangeCredentialEnc } : {}),
        ...(showcaseAiCredentialEnc ? { showcaseAiCredentialEnc } : {}),
        showcaseBotPublicUrl: CANONICAL_SHOWCASE_BOT_URL,
        showcaseCredentialsUpdatedAt: new Date(),
        updatedByUserId: userId,
      },
    });

    return this.getCredentialsStatus();
  }

  async clearShowcaseCredentials(userId: string, target: 'exchange' | 'ai' | 'all') {
    const data: Record<string, null> = {};
    if (target === 'exchange' || target === 'all') data.showcaseExchangeCredentialEnc = null;
    if (target === 'ai' || target === 'all') data.showcaseAiCredentialEnc = null;
    await this.prisma.platformSettings.update({
      where: { id: 'default' },
      data: {
        ...data,
        showcaseBotPublicUrl: CANONICAL_SHOWCASE_BOT_URL,
        updatedByUserId: userId,
        showcaseCredentialsUpdatedAt: new Date(),
      },
    });
    return this.getCredentialsStatus();
  }

  /**
   * The legacy API-to-Railway credential push is intentionally retired.
   * Fly secrets are deployment credentials and must never be copied into a
   * second runtime from a public application endpoint.
   */
  async pushToCanonicalRuntime(_userId: string): Promise<{
    ok: false;
    retired: true;
    message: string;
    serviceName: string;
    variablesSet: never[];
  }> {
    return {
      ok: false,
      retired: true,
      message:
        'Legacy Railway runtime push is retired. The canonical Fly.io bot uses reviewed Fly deployment secrets.',
      serviceName: 'doxed-btc-bot',
      variablesSet: [],
    };
  }
}
