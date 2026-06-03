import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  EXCHANGE_PROVIDER_LABELS,
  TRADING_AGENT_AI_PROVIDER_LABELS,
  exchangeBotRuntimeNote,
  exchangeCredentialsToEnvVars,
  exchangeRequiresPassphrase,
  type ExchangeProvider,
  type TradingAgentAiProvider,
} from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';
import { ExchangeAdapterRegistry } from '../exchanges/exchange-adapter.registry';
import type { ExchangeCredentials } from '../exchanges/exchange-adapter.interface';

const RAILWAY_GQL = 'https://backboard.railway.com/graphql/v2';

type ShowcaseExchangePayload = {
  apiKey: string;
  apiSecret: string;
  passphrase?: string;
  testnet?: boolean;
};

@Injectable()
export class ShowcaseRuntimeService {
  private readonly logger = new Logger(ShowcaseRuntimeService.name);
  private readonly registry = new ExchangeAdapterRegistry();

  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
    private readonly config: ConfigService,
  ) {}

  async getCredentialsStatus() {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const exchangeProvider = (row?.showcaseExchangeProvider ?? 'bybit') as ExchangeProvider;
    const aiProvider = (row?.showcaseAiProvider ?? 'deepseek') as TradingAgentAiProvider;

    return {
      exchangeProvider,
      exchangeLabel: EXCHANGE_PROVIDER_LABELS[exchangeProvider] ?? exchangeProvider,
      aiProvider,
      aiLabel: TRADING_AGENT_AI_PROVIDER_LABELS[aiProvider] ?? aiProvider,
      exchangeConfigured: Boolean(row?.showcaseExchangeCredentialEnc),
      aiConfigured: Boolean(row?.showcaseAiCredentialEnc),
      botPublicUrl: row?.showcaseBotPublicUrl ?? null,
      credentialsUpdatedAt: row?.showcaseCredentialsUpdatedAt?.toISOString() ?? null,
      runtimePushedAt: row?.showcaseRuntimePushedAt?.toISOString() ?? null,
      botRuntimeNote: exchangeBotRuntimeNote(exchangeProvider),
      aiRuntimeNote:
        aiProvider !== 'deepseek'
          ? 'Live bot reads DEEPSEEK_API_KEY — we also push your selected provider key when possible.'
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
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const exchangeProvider = (input.exchangeProvider ??
      row?.showcaseExchangeProvider ??
      'bybit') as ExchangeProvider;
    const aiProvider = (input.aiProvider ?? row?.showcaseAiProvider ?? 'deepseek') as TradingAgentAiProvider;

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

    const botUrl = input.botPublicUrl?.trim();
    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        showcaseExchangeProvider: exchangeProvider,
        showcaseAiProvider: aiProvider,
        showcaseExchangeCredentialEnc,
        showcaseAiCredentialEnc,
        showcaseBotPublicUrl: botUrl || null,
        showcaseCredentialsUpdatedAt: new Date(),
        updatedByUserId: userId,
      },
      update: {
        ...(input.exchangeProvider ? { showcaseExchangeProvider: exchangeProvider } : {}),
        ...(input.aiProvider ? { showcaseAiProvider: aiProvider } : {}),
        ...(showcaseExchangeCredentialEnc ? { showcaseExchangeCredentialEnc } : {}),
        ...(showcaseAiCredentialEnc ? { showcaseAiCredentialEnc } : {}),
        ...(botUrl !== undefined ? { showcaseBotPublicUrl: botUrl || null } : {}),
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
      data: { ...data, updatedByUserId: userId, showcaseCredentialsUpdatedAt: new Date() },
    });
    return this.getCredentialsStatus();
  }

  async pushToRailwayRuntime(userId: string): Promise<{
    ok: boolean;
    message: string;
    serviceName?: string;
    variablesSet?: string[];
  }> {
    const token =
      this.config.get<string>('RAILWAY_TOKEN')?.trim() ||
      process.env.RAILWAY_API_TOKEN?.trim();
    if (!token) {
      return {
        ok: false,
        message:
          'RAILWAY_TOKEN not set on API service — add it in Railway (API) variables or vault, then retry.',
      };
    }

    const settings = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    if (!settings?.showcaseExchangeCredentialEnc) {
      throw new BadRequestException('Save showcase exchange API keys before pushing to runtime');
    }

    const botServiceName =
      this.config.get<string>('BTC_BOT_RAILWAY_SERVICE')?.trim() || 'btc-conservative-agent';
    const apiServiceName =
      this.config.get<string>('API_RAILWAY_SERVICE')?.trim() || 'doxed-founders-website';

    const vars = await this.buildRailwayEnvVarsPromise(settings);

    try {
      await this.railwayUpsertVars(token, botServiceName, vars);
      if (settings.showcaseBotPublicUrl?.trim()) {
        await this.railwayUpsertVars(token, apiServiceName, {
          TRADING_AGENT_BOT_URL: settings.showcaseBotPublicUrl.replace(/\/$/, ''),
          CONSERVATIVE_BTC_BOT_URL: settings.showcaseBotPublicUrl.replace(/\/$/, ''),
        });
      }
      await this.prisma.platformSettings.update({
        where: { id: 'default' },
        data: {
          showcaseRuntimePushedAt: new Date(),
          updatedByUserId: userId,
        },
      });
      return {
        ok: true,
        message: `Credentials pushed to Railway (${botServiceName}) and redeploy triggered.`,
        serviceName: botServiceName,
        variablesSet: Object.keys(vars),
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.error(`Railway push failed: ${msg}`);
      return {
        ok: false,
        message: msg,
      };
    }
  }

  private async buildRailwayEnvVarsPromise(
    settings: NonNullable<Awaited<ReturnType<typeof this.prisma.platformSettings.findUnique>>>,
  ) {
    const vars: Record<string, string> = { PORT: '5000' };

    const exchangeJson = this.crypto.decrypt(settings.showcaseExchangeCredentialEnc);
    if (exchangeJson) {
      const ex = JSON.parse(exchangeJson) as ShowcaseExchangePayload;
      const provider = (settings.showcaseExchangeProvider ?? 'bybit') as ExchangeProvider;
      Object.assign(vars, exchangeCredentialsToEnvVars(provider, ex));
    }

    const aiKey = this.crypto.decrypt(settings.showcaseAiCredentialEnc);
    if (aiKey) {
      const provider = settings.showcaseAiProvider ?? 'deepseek';
      const map: Record<string, string> = {
        deepseek: 'DEEPSEEK_API_KEY',
        openai: 'OPENAI_API_KEY',
        claude: 'ANTHROPIC_API_KEY',
        gemini: 'GEMINI_API_KEY',
        openrouter: 'OPENROUTER_API_KEY',
      };
      const envName = map[provider] ?? 'DEEPSEEK_API_KEY';
      vars[envName] = aiKey;
      if (envName !== 'DEEPSEEK_API_KEY') vars.DEEPSEEK_API_KEY = aiKey;
    }

    return vars;
  }

  private async railwayGql(token: string, query: string, variables: Record<string, unknown> = {}) {
    const res = await fetch(RAILWAY_GQL, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, variables }),
    });
    const json = (await res.json()) as { data?: unknown; errors?: { message: string }[] };
    if (json.errors?.length) {
      throw new Error(json.errors.map((e) => e.message).join('; '));
    }
    return json.data as Record<string, unknown>;
  }

  private async railwayUpsertVars(
    token: string,
    serviceName: string,
    variables: Record<string, string>,
  ) {
    const data = await this.railwayGql(
      token,
      `query {
        projects { edges { node {
          id name
          environments { edges { node { id name } } }
          services { edges { node { id name } } }
        } } }
      }`,
    );

    const projects =
      (data.projects as { edges?: { node: Record<string, unknown> }[] })?.edges?.map((e) => e.node) ??
      [];
    const target = projects.find((p) =>
      (p.services as { edges?: { node: { name: string } }[] })?.edges?.some(
        (s) => s.node.name === serviceName,
      ),
    );
    if (!target) {
      throw new Error(
        `Railway service "${serviceName}" not found — create the bot service or set BTC_BOT_RAILWAY_SERVICE`,
      );
    }

    const env =
      (target.environments as { edges?: { node: { id: string; name: string } }[] })?.edges?.find(
        (e) => e.node.name === 'production',
      )?.node ??
      (target.environments as { edges?: { node: { id: string } }[] })?.edges?.[0]?.node;
    const service = (
      target.services as { edges?: { node: { id: string; name: string } }[] }
    )?.edges?.find((e) => e.node.name === serviceName)?.node;

    if (!env || !service) throw new Error('Missing Railway environment or service');

    await this.railwayGql(
      token,
      `mutation($input: VariableCollectionUpsertInput!) {
        variableCollectionUpsert(input: $input)
      }`,
      {
        input: {
          projectId: target.id as string,
          environmentId: env.id,
          serviceId: service.id,
          variables,
          replace: false,
        },
      },
    );

    await this.railwayGql(
      token,
      `mutation($serviceId: String!, $environmentId: String!) {
        serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
      }`,
      { serviceId: service.id, environmentId: env.id },
    );
  }
}
