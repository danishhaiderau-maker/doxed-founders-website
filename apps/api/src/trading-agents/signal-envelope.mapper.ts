import type { SignalIntentEnvelope } from '@dcf/utils';
import type { BotApiState } from './bot-state.mapper';

const DEFAULT_STOP_LOSS_MARGIN_PCT = -18;
const DEFAULT_LEVERAGE_HINT = 20;
const LIMIT_TTL_SEC = 1800;
const DEFAULT_PULLBACK_PCT = 0.2;

export type BotApproveSnapshot = {
  trade_id?: string;
  status?: string;
  reason?: string;
  edge_at_approve?: number;
  effective_threshold?: number;
};

export function extractBotApproveSnapshot(bot: BotApiState): BotApproveSnapshot | null {
  const raw = (bot as BotApiState & { last_approve_outcome?: BotApproveSnapshot }).last_approve_outcome;
  if (!raw?.trade_id) return null;
  return raw;
}

function resolveDirection(bot: BotApiState): 'LONG' | 'SHORT' | null {
  const dir = (
    bot.last_ai?.final_direction ??
    bot.last_ai?.direction ??
    ''
  )
    .toString()
    .toUpperCase();
  if (dir === 'LONG' || dir === 'SHORT') return dir;
  return null;
}

function resolveOffsetPct(bot: BotApiState, direction: 'LONG' | 'SHORT'): number {
  const signals = bot.signal_info?.signals ?? [];
  const active = signals.find((s) => s.status === 'ORDERED' || s.status === 'ACTIVE' || s.status === 'PENDING');
  const pull =
    active?.pullback_pct ??
    active?.pull_req ??
    (bot as BotApiState & { pullback_threshold?: number }).pullback_threshold;
  let pct = typeof pull === 'number' && pull > 0 ? pull : DEFAULT_PULLBACK_PCT / 100;
  if (pct > 1) pct = pct / 100;
  const signed = direction === 'LONG' ? -Math.abs(pct * 100) : Math.abs(pct * 100);
  return Math.round(signed * 10000) / 10000;
}

function resolveEntryModeSource(bot: BotApiState): string {
  const signals = bot.signal_info?.signals ?? [];
  const active = signals.find((s) => s.status === 'ORDERED' || s.status === 'ACTIVE');
  const mode = (active as { entry_mode?: string } | undefined)?.entry_mode;
  return mode ?? 'PULLBACK_PCT';
}

export function buildIntentEnvelope(
  cycleId: string,
  tradeId: string,
  bot: BotApiState,
): SignalIntentEnvelope | null {
  const direction = resolveDirection(bot);
  if (!direction) return null;
  const lao = extractBotApproveSnapshot(bot);
  if (lao?.status === 'BLOCKED') return null;

  const offsetPct = resolveOffsetPct(bot, direction);
  const edge =
    lao?.edge_at_approve ??
    bot.debug_state?.last_edge_score ??
    bot.last_edge ??
    0;
  const aiWin = bot.last_ai?.win_prob ?? 0;

  return {
    schema: 'dcf-signal-intent/v1',
    cycleId,
    signalId: tradeId,
    version: bot.bot_version ?? 'unknown',
    action: 'ENTER',
    direction,
    entry: {
      type: 'LIMIT',
      mode: resolveEntryModeSource(bot).includes('EMA') ? 'EMA_OFFSET_PCT' : 'PULLBACK_PCT',
      offset_pct: offsetPct,
      reference: 'SUBSCRIBER_MARK_AT_RECEIPT',
      ttl_sec: LIMIT_TTL_SEC,
    },
    risk: {
      stop_loss_margin_pct: DEFAULT_STOP_LOSS_MARGIN_PCT,
      take_profit_ladder: [
        { at_margin_pct: 8, close_position_pct: 5 },
        { at_margin_pct: 15, close_position_pct: 10 },
      ],
      leverage_hint:
        (bot as BotApiState & { leverage?: number }).leverage ?? DEFAULT_LEVERAGE_HINT,
    },
    context: {
      regime: bot.regime ?? 'UNKNOWN',
      edge: Number(edge),
      ai_win_prob: Number(aiWin),
      entry_mode_source: resolveEntryModeSource(bot),
      research_venue: 'bitfinex',
      disclaimer:
        'Research alpha from Bitfinex pipeline. Execute on your venue using your local mark at receipt. Exchange stop required at fill.',
    },
  };
}

export function buildManageEnvelope(cycleId: string, kind: 'UPDATE_STOPS' | 'EXIT_URGENT', extra?: Record<string, unknown>) {
  return {
    schema: 'dcf-signal-manage/v1',
    cycleId,
    event: kind,
    ...extra,
  };
}
