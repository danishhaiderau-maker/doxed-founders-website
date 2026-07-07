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
    // Use the SAME state-fetch the public dashboard uses, so the status dot
    // and the dashboard's botConnected flag can never disagree. The previous
    // isReachable() + fetchState() chain probed the tunnel twice with
    // different paths/timeouts and intermittently returned 'offline' even
    // while the dashboard was streaming fresh live trades from the same bot.
    // fetchPublicShowcaseState(true) already has the full reachability +
    // 10-minute relay-snapshot fallback chain baked in.
    const state = await this.botBridge.fetchPublicShowcaseState(true);
    if (state) {
      if (state.execution_paused) {
        const reason = state.execution_reason ?? '';
        if (reason === 'ADMIN_MANUAL') {
          return { status: 'offline', label: 'Showcase bot stopped by admin' };
        }
        return { status: 'updating', label: 'Agent updating' };
      }
      return { status: 'online', label: 'Agent online' };
    }

    // Tunnel AND 10-min relay cache both missed. Before declaring offline,
    // check the in-memory last-successful-fetch timestamp on the same
    // BotBridge instance the dashboard polls every few seconds. If the
    // dashboard (or any other caller in this NestJS process) pulled live
    // state from the bot within the last 5 minutes, the bot is by
    // definition alive and pushing data — the only thing failing right now
    // is THIS specific tunnel probe, which flaps because Railway's network
    // to the home Cloudflare tunnel is intermittent (~50% packet loss at
    // peak). Cutoff: 5 minutes — generous enough to absorb tunnel blips,
    // strict enough that a genuinely dead bot still goes red quickly.
    const lastLiveAt = this.botBridge.getLastLiveFetchAt();
    if (lastLiveAt > 0 && Date.now() - lastLiveAt < 5 * 60_000) {
      return { status: 'online', label: 'Agent online' };
    }
    return { status: 'offline', label: 'Showcase bot offline (stopped on Railway)' };
  }

  async getAgentControlOverview() {
    const [bridge, agent, settings, credentials] = await Promise.all([
      this.tradingAgents.getBotBridgeStatusAdmin(),
      this.prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } }),
      this.prisma.platformSettings.findUnique({ where: { id: 'default' } }),
      this.showcaseRuntime.getCredentialsStatus(),
    ]);
    const botState = bridge.botState ?? null;

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
        subscriberMaxMarginUsd: settings?.subscriberMaxMarginUsd ?? 20,
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

  private async isHomeHostedShowcase(): Promise<boolean> {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const url = row?.showcaseBotPublicUrl?.trim() ?? this.botBridge.getBotUrl() ?? '';
    if (!url) return false;
    return (
      url.includes('trycloudflare.com') ||
      url.includes('bot.doxxedcrypto.digital') ||
      url.includes('127.0.0.1') ||
      url.includes('10.0.0.102')
    );
  }

  async pauseAgentTrading() {
    // Save bot state before kill (best-effort — may fail if already stopping)
    await this.botBridge.proxyBotPost('/api/pause', {}).catch(() => undefined);

    if (await this.isHomeHostedShowcase()) {
      const res = await this.botBridge.proxyBotPost('/api/pause', {});
      const data = (res.data ?? {}) as Record<string, unknown>;
      const paused =
        data.execution_paused === true ||
        data.status === 'paused' ||
        data.execution_reason === 'ADMIN_MANUAL';
      this.botBridge.invalidateCache();
      return {
        ...res,
        ok: paused,
        paused,
        killed: false,
        message: paused
          ? 'Home bot paused — execution stopped until you resume.'
          : 'Pause failed — is the global showcase bot running on :7002?',
      };
    }

    const rail = await this.showcaseRuntime.stopShowcaseDeployment();
    this.botBridge.invalidateCache();
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
    if (await this.isHomeHostedShowcase()) {
      const health = await this.botBridge.fetchHealth();
      if (!health) {
        return {
          ok: false,
          error:
            'Home bot offline. On this PC run START-LAUNCHER.cmd once, then click Start home stack on this PC (or START-HOME.cmd).',
          resumed: false,
        };
      }
      const res = await this.botBridge.proxyBotPost('/api/resume', {});
      const data = (res.data ?? {}) as Record<string, unknown>;
      const resumed = data.execution_paused === false || data.status === 'resumed' || res.ok;
      return {
        ...res,
        ok: resumed,
        resumed,
        message: resumed
          ? 'Home bot execution resumed.'
          : 'Resume failed — check showcase bot dashboard on :7002.',
      };
    }

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
    this.botBridge.invalidateCache();
    const res = await this.botBridge.proxyBotPost('/api/reset', {});
    await this.tradingAgents.syncShowcaseMetricsFromBot().catch(() => undefined);
    const data = (res.data ?? {}) as Record<string, unknown>;
    return {
      ok: Boolean(res.ok),
      message: res.ok
        ? 'Showcase wiped — $500 clean slate, fresh collection ON'
        : String(data.error ?? res.error ?? 'Bot reset failed'),
      reset: data.reset ?? null,
    };
  }

  async updateShowcaseConfig(
    userId: string,
    input: {
      exchangeProvider?: string;
      aiProvider?: string;
      agentShowcaseDefaultSettings?: string;
      subscriberMaxMarginUsd?: number;
    },
  ) {
    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        showcaseExchangeProvider: input.exchangeProvider ?? 'bybit',
        showcaseAiProvider: input.aiProvider ?? 'deepseek',
        agentShowcaseDefaultSettings: input.agentShowcaseDefaultSettings ?? null,
        subscriberMaxMarginUsd:
          input.subscriberMaxMarginUsd != null && input.subscriberMaxMarginUsd > 0
            ? Math.round(input.subscriberMaxMarginUsd)
            : 20,
        updatedByUserId: userId,
      },
      update: {
        ...(input.exchangeProvider ? { showcaseExchangeProvider: input.exchangeProvider } : {}),
        ...(input.aiProvider ? { showcaseAiProvider: input.aiProvider } : {}),
        ...(input.agentShowcaseDefaultSettings !== undefined
          ? { agentShowcaseDefaultSettings: input.agentShowcaseDefaultSettings || null }
          : {}),
        ...(input.subscriberMaxMarginUsd != null && input.subscriberMaxMarginUsd > 0
          ? { subscriberMaxMarginUsd: Math.round(input.subscriberMaxMarginUsd) }
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
