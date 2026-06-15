import { buildAgentShowcaseFlash, type AgentShowcaseFlash } from '@dcf/utils';
import type { BotApiState } from './bot-state.mapper';

export function buildShowcaseFlashFromBot(
  bot: BotApiState | null | undefined,
  opts: { botConnected: boolean; executionPaused?: boolean },
): AgentShowcaseFlash | null {
  if (!bot && !opts.botConnected) return null;
  return buildAgentShowcaseFlash({
    botVersion: bot?.bot_version ?? null,
    botStartTime: bot?.bot_start_time ?? null,
    freshCollectionMode: bot?.fresh_collection_mode,
    tradeCountSession: bot?.trade_count_session,
    botConnected: opts.botConnected,
    executionPaused: opts.executionPaused,
  });
}
