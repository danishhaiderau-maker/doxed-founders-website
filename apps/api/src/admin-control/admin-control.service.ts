import { Injectable } from '@nestjs/common';
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
    const [bridge, agent] = await Promise.all([
      this.tradingAgents.getBotBridgeStatusAdmin(),
      this.prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } }),
    ]);

    const deepSeekConnected = Boolean(bridge.connected && bridge.deepSeekConnected);

    return {
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
}
