import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  BotActivityEntry,
  BotApiState,
  mapBotStateToActivity,
  mapBotStateToAgentStats,
  mapBotStateToDashboard,
} from './bot-state.mapper';

@Injectable()
export class BotBridgeService {
  private readonly logger = new Logger(BotBridgeService.name);
  private lastFetchAt = 0;
  private cached: BotApiState | null = null;
  private cacheMs = 3000;

  constructor(private readonly config: ConfigService) {}

  getBotUrl(): string | null {
    const url = (
      this.config.get<string>('TRADING_AGENT_BOT_URL') ??
      this.config.get<string>('CONSERVATIVE_BTC_BOT_URL') ??
      ''
    ).trim();
    return url ? url.replace(/\/$/, '') : null;
  }

  isEnabled(): boolean {
    return Boolean(this.getBotUrl());
  }

  async fetchState(force = false): Promise<BotApiState | null> {
    const base = this.getBotUrl();
    if (!base) return null;

    const now = Date.now();
    if (!force && this.cached && now - this.lastFetchAt < this.cacheMs) {
      return this.cached;
    }

    try {
      const res = await fetch(`${base}/api/state`, {
        signal: AbortSignal.timeout(8000),
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) {
        this.logger.warn(`Bot /api/state HTTP ${res.status}`);
        return this.cached;
      }
      const data = (await res.json()) as BotApiState;
      if (!data || typeof data !== 'object') return this.cached;
      this.cached = data;
      this.lastFetchAt = now;
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logger.warn(`Bot fetch failed: ${msg}`);
      return this.cached;
    }
  }

  async getLiveDashboard(agentName: string) {
    const bot = await this.fetchState();
    if (!bot) return null;
    return {
      dashboard: mapBotStateToDashboard(bot),
      stats: mapBotStateToAgentStats(bot),
      activity: mapBotStateToActivity(bot, agentName),
      botConnected: true,
      botUrl: this.getBotUrl(),
      strategyMode: bot.strategy_mode ?? 'RESEARCH',
      executionPaused: bot.execution_paused ?? false,
      executionReason: bot.execution_reason ?? null,
    };
  }

  async getLiveActivity(agentName: string): Promise<BotActivityEntry[] | null> {
    const bot = await this.fetchState();
    if (!bot) return null;
    return mapBotStateToActivity(bot, agentName);
  }
}
