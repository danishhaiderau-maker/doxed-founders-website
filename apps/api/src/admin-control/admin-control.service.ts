import { BadRequestException, Injectable } from '@nestjs/common';
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

export type PublicAgentStatus = 'online' | 'offline' | 'updating' | 'degraded';

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

    // Direct Fly state AND the fresh relay cache both missed. Before declaring offline,
    // check the in-memory last-successful-fetch timestamp on the same
    // BotBridge instance the dashboard polls every few seconds. If the
    // dashboard (or any other caller in this NestJS process) pulled live
    // state from the bot within the last 5 minutes, the bot is by
    // definition alive and pushing data — the only thing failing right now
    // is THIS specific cross-region probe. Cutoff: 5 minutes absorbs brief
    // network blips,
    // strict enough that a genuinely dead bot still goes red quickly.
    const lastLiveAt = this.botBridge.getLastLiveFetchAt();
    if (lastLiveAt > 0 && Date.now() - lastLiveAt < 5 * 60_000) {
      const ageSec = Math.max(1, Math.round((Date.now() - lastLiveAt) / 1_000));
      return {
        status: 'degraded',
        label: `Fly feed degraded — last verified ${ageSec}s ago`,
      };
    }
    return { status: 'offline', label: 'Canonical Fly bot is unreachable' };
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
    const exchangeProvider = 'bitfinex' as ExchangeProvider;
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
        railwayPushReady: false,
        canonicalRuntime: 'fly.io',
      },
    };
  }

  async pauseAgentTrading() {
    // Pause new entries inside the canonical Fly process. The process itself
    // remains alive so exposure reconciliation, exits, and state publishing
    // continue while trading is disarmed.
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
      runtime: 'fly.io',
      message: paused
        ? 'Fly trading entries paused; monitoring and risk management remain online.'
        : 'Pause failed because the canonical Fly bot did not confirm ADMIN_MANUAL.',
    };
  }

  async forceFlatShowcasePaper() {
    // Paper flattening is deliberately separate from the Bitfinex relay
    // emergency path. Pause first so no fresh paper entry can race the close.
    const pause = await this.botBridge.proxyBotPost('/api/pause', {});
    const pauseData = (pause.data ?? {}) as Record<string, unknown>;
    const paused =
      pause.ok === true &&
      (pauseData.execution_paused === true || pauseData.status === 'paused');
    if (!paused) {
      return {
        ok: false,
        paused: false,
        closedPositions: 0,
        remainingPositions: null,
        remainingOrders: null,
        message: 'Paper force-flat stopped because Fly did not confirm the paused entry gate.',
      };
    }

    this.botBridge.invalidateCache();
    const before = await this.botBridge.fetchPublicShowcaseState(true);
    if (!before) {
      return {
        ok: false,
        paused: true,
        closedPositions: 0,
        remainingPositions: null,
        remainingOrders: null,
        message: 'Fly is paused, but its exact paper book could not be read; no blind closes were sent.',
      };
    }

      const tradeIds = Array.from(
      new Set(
        (before.positions ?? [])
          .map((position) => String(position.trade_id ?? '').trim())
          .filter(Boolean),
      ),
      );
      const orderTradeIds = Array.from(
        new Set(
          (before.orders ?? [])
            .map((order) => String(order.trade_id ?? '').trim())
            .filter(Boolean),
        ),
      );
      let cancelledOrders = 0;
      for (const tradeId of orderTradeIds) {
        const cancel = await this.botBridge.proxyBotPost('/api/orders/cancel', {
          trade_id: tradeId,
        });
        if (cancel.ok) cancelledOrders += 1;
      }
      let closedPositions = 0;
    for (const tradeId of tradeIds) {
      const close = await this.botBridge.proxyBotPost('/api/positions/close', {
        trade_id: tradeId,
      });
      if (close.ok) closedPositions += 1;
    }

    this.botBridge.invalidateCache();
    const after = await this.botBridge.fetchPublicShowcaseState(true);
    const remainingPositions = after?.positions?.length ?? null;
    const remainingOrders = after?.orders?.length ?? null;
    const flat = remainingPositions === 0 && remainingOrders === 0;
    return {
      ok: flat,
        paused: true,
        cancelledOrders,
        closedPositions,
      remainingPositions,
      remainingOrders,
      message: flat
          ? `Paper book is flat and paused; cancelled ${cancelledOrders} order(s) and closed ${closedPositions} position(s).`
        : 'Paper book is still not provably flat; keep trading paused and inspect the remaining rows.',
    };
  }

  async resumeAgentTrading() {
    const health = await this.botBridge.fetchHealth();
    if (!health) {
      return {
        ok: false,
        error: 'Canonical Fly bot is unreachable; trading remains paused.',
        resumed: false,
        runtime: 'fly.io',
      };
    }
    const res = await this.botBridge.proxyBotPost('/api/resume', {});
    const data = (res.data ?? {}) as Record<string, unknown>;
    this.botBridge.invalidateCache();
    const responseConfirmed = res.ok === true && data.execution_paused === false;
    const confirmedState = responseConfirmed
      ? await this.botBridge.fetchPublicShowcaseState(true).catch(() => null)
      : null;
    const resumed = responseConfirmed && confirmedState?.execution_paused === false;
    return {
      ...res,
      ok: resumed,
      resumed,
      runtime: 'fly.io',
      message: resumed
        ? 'Canonical Fly trading entry gate resumed.'
        : responseConfirmed
          ? 'Resume held because a fresh canonical Fly state did not confirm the unpaused gate.'
          : 'Resume failed because the canonical Fly bot response did not explicitly confirm execution_paused=false.',
    };
  }

  async restartAgentRuntime() {
    return {
      ok: false,
      retired: true,
      runtime: 'fly.io',
      message:
        'Ad-hoc runtime restart is retired. Fly releases are revision-locked and trading pause/resume is controlled separately.',
    };
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
    if (input.exchangeProvider && input.exchangeProvider !== 'bitfinex') {
      throw new BadRequestException(
        'Conservative BTC is locked to Bitfinex; another showcase exchange cannot be selected.',
      );
    }
    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: {
        id: 'default',
        showcaseExchangeProvider: input.exchangeProvider ?? 'bitfinex',
        showcaseAiProvider: input.aiProvider ?? 'deepseek',
        agentShowcaseDefaultSettings: input.agentShowcaseDefaultSettings ?? null,
        subscriberMaxMarginUsd:
          input.subscriberMaxMarginUsd != null && input.subscriberMaxMarginUsd > 0
            ? Math.round(input.subscriberMaxMarginUsd)
            : 20,
        updatedByUserId: userId,
      },
      update: {
        showcaseExchangeProvider: 'bitfinex',
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
