import { Injectable } from '@nestjs/common';
import {
  EXCHANGE_PROVIDER_LABELS,
  TRADING_AGENT_AI_PROVIDER_LABELS,
  type ExchangeProvider,
  type TradingAgentAiProvider,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { BotBridgeService } from '../trading-agents/bot-bridge.service';
import { TradingAgentsService } from '../trading-agents/trading-agents.service';
import { PLATFORM_X_SHARE_FOOTER } from '@dcf/utils';

export type PublicAgentStatus = 'online' | 'offline' | 'updating';

@Injectable()
export class AdminControlService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly botBridge: BotBridgeService,
    private readonly tradingAgents: TradingAgentsService,
  ) {}

  async getShareFooter(): Promise<string> {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    return row?.globalShareFooter?.trim() || PLATFORM_X_SHARE_FOOTER;
  }

  async updateShareFooter(userId: string, footer: string) {
    const globalShareFooter = footer.trim() || null;
    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: { id: 'default', globalShareFooter, updatedByUserId: userId },
      update: { globalShareFooter, updatedByUserId: userId },
    });
    return { globalShareFooter: globalShareFooter ?? PLATFORM_X_SHARE_FOOTER };
  }

  async getPublicAgentStatus(): Promise<{ status: PublicAgentStatus; label: string }> {
    const enabled = this.botBridge.isEnabled();
    if (!enabled) {
      return { status: 'offline', label: 'Agent offline' };
    }
    const state = await this.botBridge.fetchState();
    if (!state) {
      return { status: 'offline', label: 'Agent offline' };
    }
    if (state.execution_paused) {
      return { status: 'updating', label: 'Agent updating' };
    }
    return { status: 'online', label: 'Agent online' };
  }

  async getAgentControlOverview() {
    const [bridge, agent, settings, botState] = await Promise.all([
      this.tradingAgents.getBotBridgeStatusAdmin(),
      this.prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } }),
      this.prisma.platformSettings.findUnique({ where: { id: 'default' } }),
      this.botBridge.fetchState(),
    ]);

    const deepSeekConnected = Boolean(bridge.connected && bridge.deepSeekConnected);
    const exchangeProvider = (settings?.showcaseExchangeProvider ?? 'bybit') as ExchangeProvider;
    const aiProvider = (settings?.showcaseAiProvider ?? 'deepseek') as TradingAgentAiProvider;

    const openPos = botState?.positions?.[0];
    const lastDecision =
      openPos?.dir ?? openPos?.side ?? botState?.last_ai?.decision ?? botState?.debug_state?.last_pipeline_stage ?? '—';
    const lastAiOpinion =
      botState?.last_ai?.comment ??
      botState?.last_ai?.reason ??
      botState?.regime ??
      '—';

    return {
      showcase: {
        exchangeProvider,
        exchangeLabel: EXCHANGE_PROVIDER_LABELS[exchangeProvider] ?? exchangeProvider,
        aiProvider,
        aiLabel: TRADING_AGENT_AI_PROVIDER_LABELS[aiProvider] ?? aiProvider,
        note: 'Admin keys power the public showcase only — never user instances.',
      },
      adapters: {
        exchangeStatus: bridge.connected ? 'connected' : 'disconnected',
        marketDataStatus: botState?.ws_ready || botState?.diag?.ws_status ? 'connected' : 'unknown',
        aiStatus: deepSeekConnected ? 'connected' : 'unknown',
        simulationStatus: botState?.execution_paused
          ? 'paused'
          : bridge.connected
            ? 'running'
            : 'offline',
        lastDecision: String(lastDecision).toUpperCase(),
        lastAiOpinion: String(lastAiOpinion),
        lastMarketUpdate: bridge.lastFetchAt,
      },
      agent: agent
        ? {
            name: agent.name,
            slug: agent.slug,
            status: agent.status,
            balanceUsd: Number(agent.balanceUsd),
            equityUsd: Number(agent.equityUsd),
            netReturnPct: Number(agent.netReturnPct),
            followerCount: agent.followerCount,
            costDdollarDay: agent.costDdollarDay,
          }
        : null,
      runtime: {
        publicStatus: bridge.publicStatus,
        bridgeEnabled: bridge.enabled,
        connected: bridge.connected,
        strategyMode: bridge.strategyMode,
        executionPaused: bridge.executionPaused,
        executionReason: bridge.executionReason,
        price: bridge.price,
        wsHealth: bridge.wsHealth,
        deepSeekConnected,
        deployVersion: bridge.appVersion,
        lastFetchAt: bridge.lastFetchAt,
      },
      infrastructure: {
        botConfigured: bridge.enabled,
        botReachable: bridge.connected,
        runtimeHost: bridge.stateEndpoint,
        websocketStatus: bridge.wsHealth,
        deepSeekStatus: deepSeekConnected ? 'connected' : 'unknown',
      },
    };
  }

  async pauseAgentTrading() {
    return this.botBridge.proxyBotPost('/api/pause', {});
  }

  async resumeAgentTrading() {
    return this.botBridge.proxyBotPost('/api/resume', {});
  }

  async restartAgentRuntime() {
    return this.botBridge.proxyBotPost('/api/resume', { restart: true });
  }

  async resetShowcaseSimulation() {
    return {
      ok: false,
      message:
        'Reset simulation preserves historical trades in DB. Wire bot /api/reset when ready.',
    };
  }

  async updateShowcaseConfig(
    userId: string,
    input: { exchangeProvider?: string; aiProvider?: string },
  ) {
    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        showcaseExchangeProvider: input.exchangeProvider ?? 'bybit',
        showcaseAiProvider: input.aiProvider ?? 'deepseek',
        updatedByUserId: userId,
      },
      update: {
        ...(input.exchangeProvider ? { showcaseExchangeProvider: input.exchangeProvider } : {}),
        ...(input.aiProvider ? { showcaseAiProvider: input.aiProvider } : {}),
        updatedByUserId: userId,
      },
    });
    return this.getAgentControlOverview();
  }
}
