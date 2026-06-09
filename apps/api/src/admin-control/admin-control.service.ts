import { Injectable } from '@nestjs/common';
import { ShowcaseRuntimeService } from './showcase-runtime.service';
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
    private readonly showcaseRuntime: ShowcaseRuntimeService,
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
    const state = await this.botBridge.fetchState(true);
    if (!state) {
      return { status: 'offline', label: 'Showcase bot offline (stopped on Railway)' };
    }
    if (state.execution_paused) {
      const reason = state.execution_reason ?? '';
      if (reason === 'ADMIN_MANUAL') {
        return { status: 'offline', label: 'Showcase bot stopped by admin' };
      }
      return { status: 'updating', label: 'Agent updating' };
    }
    return { status: 'online', label: 'Agent online' };
  }

  async getAgentControlOverview() {
    const [bridge, agent, settings, botState, credentials] = await Promise.all([
      this.tradingAgents.getBotBridgeStatusAdmin(),
      this.prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } }),
      this.prisma.platformSettings.findUnique({ where: { id: 'default' } }),
      this.botBridge.fetchState(),
      this.showcaseRuntime.getCredentialsStatus(),
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
        exchangeConfigured: credentials.exchangeConfigured,
        aiConfigured: credentials.aiConfigured,
        botPublicUrl: credentials.botPublicUrl,
        credentialsUpdatedAt: credentials.credentialsUpdatedAt,
        runtimePushedAt: credentials.runtimePushedAt,
        botRuntimeNote: credentials.botRuntimeNote,
        aiRuntimeNote: credentials.aiRuntimeNote,
        agentShowcaseDefaultSettings: settings?.agentShowcaseDefaultSettings?.trim() ?? null,
      },
      adapters: {
        exchangeStatus: bridge.connected
          ? 'connected'
          : credentials.exchangeConfigured
            ? 'configured (awaiting bridge)'
            : 'disconnected',
        marketDataStatus: botState?.ws_ready || botState?.diag?.ws_status ? 'connected' : 'unknown',
        aiStatus: deepSeekConnected
          ? 'connected'
          : credentials.aiConfigured
            ? 'configured (awaiting bridge)'
            : 'unknown',
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
        botConfigured: bridge.enabled || Boolean(credentials.botPublicUrl),
        botReachable: bridge.connected,
        runtimeHost: bridge.stateEndpoint ?? credentials.botPublicUrl,
        websocketStatus: bridge.wsHealth,
        deepSeekStatus: deepSeekConnected
          ? 'connected'
          : credentials.aiConfigured
            ? 'key saved'
            : 'unknown',
        railwayPushReady:
          credentials.exchangeConfigured &&
          credentials.aiConfigured &&
          Boolean(credentials.botPublicUrl),
      },
    };
  }

  async pauseAgentTrading() {
    // Save bot state before kill (best-effort — may fail if already stopping)
    await this.botBridge.proxyBotPost('/api/pause', {}).catch(() => undefined);

    const rail = await this.showcaseRuntime.stopShowcaseDeployment();
    if (rail.ok) {
      return {
        ok: true,
        paused: true,
        killed: true,
        message: rail.message,
        deploymentId: rail.deploymentId,
      };
    }

    const res = await this.botBridge.proxyBotPost('/api/pause', {});
    const data = (res.data ?? {}) as Record<string, unknown>;
    const paused =
      data.execution_paused === true ||
      data.status === 'paused' ||
      data.execution_reason === 'ADMIN_MANUAL';
    return {
      ...res,
      ok: paused,
      paused,
      killed: false,
      message: rail.message || (paused ? 'Trading paused (Railway still running)' : 'Stop failed'),
    };
  }

  async resumeAgentTrading() {
    const rail = await this.showcaseRuntime.startShowcaseDeployment();
    if (!rail.ok) {
      return { ok: false, error: rail.message, resumed: false };
    }

    // Bot needs time to boot before /api/resume
    await new Promise((r) => setTimeout(r, 8000));
    const res = await this.botBridge.proxyBotPost('/api/resume', {});
    const data = (res.data ?? {}) as Record<string, unknown>;
    const resumed = data.execution_paused === false || data.status === 'resumed' || res.ok;
    return {
      ...res,
      ok: rail.ok,
      resumed,
      message: rail.message,
    };
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
    input: { exchangeProvider?: string; aiProvider?: string; agentShowcaseDefaultSettings?: string },
  ) {
    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        showcaseExchangeProvider: input.exchangeProvider ?? 'bybit',
        showcaseAiProvider: input.aiProvider ?? 'deepseek',
        agentShowcaseDefaultSettings: input.agentShowcaseDefaultSettings ?? null,
        updatedByUserId: userId,
      },
      update: {
        ...(input.exchangeProvider ? { showcaseExchangeProvider: input.exchangeProvider } : {}),
        ...(input.aiProvider ? { showcaseAiProvider: input.aiProvider } : {}),
        ...(input.agentShowcaseDefaultSettings !== undefined
          ? { agentShowcaseDefaultSettings: input.agentShowcaseDefaultSettings || null }
          : {}),
        updatedByUserId: userId,
      },
    });
    return this.getAgentControlOverview();
  }

  async getAgentDefaultSettings(): Promise<string | null> {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    return row?.agentShowcaseDefaultSettings?.trim() || null;
  }
}
