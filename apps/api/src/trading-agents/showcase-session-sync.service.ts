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
  showcaseSessionBotVersion?: string;
  showcaseSessionFreshResetTs?: number;
};

/** Sever identity = botVersion + freshResetTs ONLY (bot_start_time excluded).
 *  A bare bot crash/auto-restart changes bot_start_time but the bot re-adopts its
 *  own book and resumes the same trades — severing every PENDING copy participant
 *  on each restart orphaned resting orders and missed trades. Only a fresh
 *  collection (last_fresh_reset_ts) or a version change is a REAL reset.
 *  bot_start_time keeps flowing into the full epoch key + dashboard fields
 *  (uptime/staleness displays are untouched); it just no longer triggers
 *  resetAllUserCopySessions. */
function severKeyFromEpoch(epoch: ShowcaseSessionEpoch): string {
  return `${epoch.botVersion}|${epoch.freshResetTs}`;
}

/** Recover the previous sever key from the persisted dashboard fields, falling
 *  back to parsing the stored epoch key (`version|startTime|freshResetTs`). */
function severKeyFromStored(dash: StoredEpochMeta, prevKey: string): string | null {
  const version = dash.showcaseSessionBotVersion;
  const freshResetTs = dash.showcaseSessionFreshResetTs;
  if (typeof version === 'string' && version.length > 0 && typeof freshResetTs === 'number') {
    return `${version}|${freshResetTs}`;
  }
  const parts = prevKey.split('|');
  if (parts.length >= 3) {
    return `${parts.slice(0, parts.length - 2).join('|')}|${parts[parts.length - 1]}`;
  }
  return null;
}

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

    // Bare restart (bot_start_time changed; version + fresh-reset ts unchanged):
    // the bot re-adopts its own book and resumes the same trades. Persist the new
    // epoch key so we stop re-detecting, but do NOT sever user copy sessions.
    const prevSeverKey = severKeyFromStored(dash, prevKey);
    if (prevSeverKey != null && prevSeverKey === severKeyFromEpoch(epoch)) {
      this.logger.warn(
        `Showcase epoch restart detected (${prevKey} -> ${epoch.key}) — continuity preserved, skipping copy-session sever`,
      );
      await this.persistAgentEpoch(agent.id, dash, epoch, 'restart_continuity', {
        preserveResetStamp: true,
      });
      return;
    }

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
    dash: Record<string, unknown> & StoredEpochMeta,
    epoch: ShowcaseSessionEpoch,
    reason: string,
    opts?: { preserveResetStamp?: boolean },
  ) {
    // Restart-continuity persists the new epoch key WITHOUT overwriting the
    // last real reset timestamp/reason shown on dashboards.
    const resetAt = opts?.preserveResetStamp
      ? (dash.showcaseSessionResetAt ?? new Date().toISOString())
      : new Date().toISOString();
    const resetReason = opts?.preserveResetStamp
      ? (dash.showcaseSessionResetReason ?? reason)
      : reason;
    await this.prisma.tradingAgent.update({
      where: { id: agentId },
      data: {
        dashboardState: {
          ...dash,
          showcaseSessionEpoch: epoch.key,
          showcaseSessionResetAt: resetAt,
          showcaseSessionResetReason: resetReason,
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
