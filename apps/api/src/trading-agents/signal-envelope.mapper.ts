import type { SignalIntentEnvelope } from '@dcf/utils';
import {
  DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
  DEFAULT_SUBSCRIBER_LEVERAGE,
  SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT,
  SHOWCASE_DETERMINISTIC_ENTRY_POLICY_VERSION,
  SUBSCRIBER_TRAIL_LADDER,
} from '@dcf/utils';
import {
  isExecutableStructuralShowcaseOrder,
  type BotApiState,
} from './bot-state.mapper';

const DEFAULT_STOP_LOSS_MARGIN_PCT = SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT;
const LIMIT_TTL_SEC = 1800;

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

function normalizeDirection(value: unknown): 'LONG' | 'SHORT' | null {
  const dir = String(value ?? '').toUpperCase();
  if (dir === 'LONG' || dir === 'SHORT') return dir;
  if (dir === 'BUY') return 'LONG';
  if (dir === 'SELL') return 'SHORT';
  return null;
}

function resolveExactCanonicalEntry(
  bot: BotApiState,
  tradeId: string,
): { direction: 'LONG' | 'SHORT'; limitPrice: number; source: string; policy: string } | null {
  const order = (bot.orders ?? []).find(
    (candidate) =>
      candidate.trade_id === tradeId
      && isExecutableStructuralShowcaseOrder(candidate),
  );
  if (order) {
    const direction = normalizeDirection(order.signal_dir ?? order.side);
    const limitPrice = Number(order.limit_price);
    // Preserve the executable policy the bot emitted. New entries carry the
    // deterministic 0.1% offset policy; legacy in-flight orders may still carry
    // micro_sr_structural_limit_v1 (both are in EXECUTABLE_ENTRY_POLICY_VERSIONS).
    const policy = String(order.entry_limit_policy ?? SHOWCASE_DETERMINISTIC_ENTRY_POLICY_VERSION);
    if (direction && Number.isFinite(limitPrice) && limitPrice > 0) {
      return { direction, limitPrice, source: 'SHOWCASE_PENDING_ORDER', policy };
    }
  }

  return null;
}

export function buildIntentEnvelope(
  cycleId: string,
  tradeId: string,
  bot: BotApiState,
  options?: { maxMarginUsd?: number },
): SignalIntentEnvelope | null {
  const lao = extractBotApproveSnapshot(bot);
  if (lao?.status === 'BLOCKED' && lao.trade_id === tradeId) return null;
  const exact = resolveExactCanonicalEntry(bot, tradeId);
  if (!exact) return null;
  const { direction, limitPrice, source, policy } = exact;
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
      mode: 'EXACT_LIMIT',
      offset_pct: 0,
      exact_limit_price: limitPrice,
      reference: 'SHOWCASE_EXACT_LIMIT',
      ttl_sec: LIMIT_TTL_SEC,
    },
    risk: {
      stop_loss_margin_pct: DEFAULT_STOP_LOSS_MARGIN_PCT,
      take_profit_ladder: SUBSCRIBER_TRAIL_LADDER.map(([at_margin_pct, lock_margin_pct]) => ({
        at_margin_pct,
        close_position_pct: lock_margin_pct,
      })),
      leverage_hint: DEFAULT_SUBSCRIBER_LEVERAGE,
      max_margin_usd: options?.maxMarginUsd ?? DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
    },
    context: {
      regime: bot.regime ?? 'UNKNOWN',
      edge: Number(edge),
      ai_win_prob: Number(aiWin),
      entry_mode_source: source,
      entry_limit_policy: policy,
      research_venue: 'bitfinex',
      disclaimer:
        'Exact canonical Bitfinex showcase limit. Exchange stop required at fill.',
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
