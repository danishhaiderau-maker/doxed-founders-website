import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { SignalCycleStatus } from '@prisma/client';
import { buildShowcaseSessionEpoch, type ShowcaseSessionEpoch } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { BotBridgeService } from './bot-bridge.service';
import type { BotApiState } from './bot-state.mapper';
import { TradingAgentInstancesService } from './trading-agent-instances.service';

const AGENT_SLUG = 'conservative-btc';
const SYNC_POLL_MS = 12_000;

type StoredEpochMeta = {
  showcaseSessionEpoch?: string;
  showcaseSessionResetAt?: string;
  showcaseSessionResetReason?: string;
};

@Injectable()
export class ShowcaseSessionSyncService implements OnModuleInit {
  private readonly logger = new Logger(ShowcaseSessionSyncService.name);
  private syncing = false;

  constructor(
    private readonly prisma: PrismaService,
    private readonly botBridge: BotBridgeService,
    private readonly instances: TradingAgentInstancesService,
  ) {}

  onModuleInit() {
    if (!this.botBridge.isEnabled()) return;
    this.logger.log(`Showcase session sync polling every ${SYNC_POLL_MS / 1000}s`);
    setInterval(() => void this.poll(), SYNC_POLL_MS);
    setTimeout(() => void this.poll(), 2_000);
  }

  private async poll() {
    if (!this.botBridge.isEnabled() || this.syncing) return;
    this.syncing = true;
    try {
      // Pin the epoch to the single canonical showcase bot (the one the relay mirrors via :7002).
      // Racing Fly + CF returns different bot_start_time values and would flip the epoch every
      // poll, wiping every user's armed relay sim via resetAllUserCopySessions.
      const bot = await this.botBridge.fetchShowcaseCanonicalState(true);
      if (!bot) return;
      await this.syncFromBotState(bot);
    } finally {
      this.syncing = false;
    }
  }

  epochFromBot(bot: BotApiState): ShowcaseSessionEpoch {
    return buildShowcaseSessionEpoch({
      botVersion: bot.bot_version ?? null,
      botStartTime: bot.bot_start_time ?? null,
      freshResetTs: bot.last_fresh_reset_ts ?? null,
    });
  }

  async syncFromBotState(bot: BotApiState) {
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: AGENT_SLUG } });
    if (!agent) return;

    const epoch = this.epochFromBot(bot);
    const dash = (agent.dashboardState ?? {}) as Record<string, unknown> & StoredEpochMeta;
    const prevKey = dash.showcaseSessionEpoch;

    if (!prevKey) {
      await this.persistAgentEpoch(agent.id, dash, epoch, 'bootstrap');
      return;
    }
    if (prevKey === epoch.key) return;

    this.logger.warn(
      `Showcase session epoch changed (${prevKey} -> ${epoch.key}) — resetting copy/relay/paper sessions`,
    );
    await this.instances.resetAllUserCopySessions({
      agentId: agent.id,
      reason: `showcase_session_epoch:${epoch.key}`,
      botStartTime: epoch.botStartTime > 0 ? epoch.botStartTime : undefined,
    });
    await this.expireStaleShowcaseCycles(agent.id);
    await this.persistAgentEpoch(agent.id, dash, epoch, 'epoch_change');
    this.botBridge.invalidateCache();
  }

  private async persistAgentEpoch(
    agentId: string,
    dash: Record<string, unknown>,
    epoch: ShowcaseSessionEpoch,
    reason: string,
  ) {
    await this.prisma.tradingAgent.update({
      where: { id: agentId },
      data: {
        dashboardState: {
          ...dash,
          showcaseSessionEpoch: epoch.key,
          showcaseSessionResetAt: new Date().toISOString(),
          showcaseSessionResetReason: reason,
          showcaseSessionBotVersion: epoch.botVersion,
          showcaseSessionBotStartTime: epoch.botStartTime,
          showcaseSessionFreshResetTs: epoch.freshResetTs,
        },
      },
    });
  }

  private async expireStaleShowcaseCycles(agentId: string) {
    const now = new Date();
    const result = await this.prisma.signalCycle.updateMany({
      where: {
        agentId,
        status: { in: [SignalCycleStatus.INTENT, SignalCycleStatus.PENDING_ENTRY] },
      },
      data: { status: SignalCycleStatus.EXPIRED, closedAt: now },
    });
    if (result.count > 0) {
      this.logger.log(`Expired ${result.count} stale showcase signal cycle(s) after session reset`);
    }
  }
}
