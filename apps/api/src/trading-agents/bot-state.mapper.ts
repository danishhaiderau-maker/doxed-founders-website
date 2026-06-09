import type { TradingAgentDashboardState } from '@dcf/utils';

/** Shape returned by the Python bot GET /api/state (subset we use). */
export type BotApiState = {
  price?: number | null;
  account_balance?: number;
  equity?: number;
  fresh_collection_mode?: boolean;
  bot_start_time?: number;
  trade_count_session?: number;
  bot_version?: string;
  daily_pnl_usd?: number;
  regime?: string;
  strategy_mode?: string;
  execution_paused?: boolean;
  execution_reason?: string;
  execution_status?: string;
  data_source?: string;
  price_source?: string;
  ws_ready?: boolean;
  data_quality?: number;
  edge_threshold?: number;
  last_edge?: number;
  ai_threshold?: number;
  support_resistance?: {
    swing_high?: number | null;
    swing_low?: number | null;
    dist_to_resistance?: number;
    dist_to_support?: number;
    sr_state?: string;
    sr_bias?: string;
  };
  last_ai?: {
    win_prob?: number | null;
    direction?: string | null;
    decision?: string | null;
    comment?: string | null;
    reason?: string | null;
    source?: string | null;
  };
  debug_state?: {
    last_edge_score?: number;
    edge_progress?: string;
    skip_reason?: string | null;
    last_block_reason?: string | null;
    last_pipeline_stage?: string | null;
  };
  funding?: {
    rate_pct_per_8h?: number;
    source?: string;
    interpretation?: string;
  };
  market_context?: {
    market_structure?: { structure_bias?: string; structure_score?: number };
    multi_tf?: { agreement?: string };
    trend_strength?: { adx?: number; trend_score?: number };
  };
  feature_snapshot?: Record<string, number>;
  diag?: { ws_status?: string };
  positions?: Array<{
    dir?: string;
    side?: string;
    entry?: number;
    qty?: number;
    sl?: number;
    tp?: number;
    pnl_pct_margin?: number;
    unreal_usd?: number;
  }>;
  orders?: Array<{
    side?: string;
    status?: string;
    limit_price?: number;
    qty?: number;
    signal_price?: number;
    age_min?: number;
  }>;
  trades?: Array<{
    ts?: string;
    trade_id?: string;
    dir?: string;
    final_direction?: string;
    entry?: number;
    exit?: number;
    pnl?: number;
    net_pnl_usd?: number;
    exit_reason?: string;
  }>;
  ai_history?: Array<{
    time?: string;
    trade_id?: string;
    decision?: string;
    win_prob?: number;
    comment?: string;
    final_direction?: string;
    ai_direction_raw?: string;
  }>;
  signal_info?: { count?: number; active?: boolean };
  bot_start_time?: number;
};

const STARTING_BALANCE = 500;

function pctDist(price: number, level: number | null | undefined): number {
  if (!price || !level || level <= 0) return 0;
  return Math.abs((level - price) / price) * 100;
}

export function mapBotStateToDashboard(bot: BotApiState): TradingAgentDashboardState {
  const price = bot.price ?? 0;
  const sr = bot.support_resistance ?? {};
  const swingHigh = sr.swing_high ?? 0;
  const swingLow = sr.swing_low ?? 0;
  const support = swingLow > 0 ? swingLow : Math.round(price * 0.995);
  const resistance = swingHigh > 0 ? swingHigh : Math.round(price * 1.008);
  const distResPct = sr.dist_to_resistance != null ? sr.dist_to_resistance * 100 : pctDist(price, resistance);
  const distSupPct = sr.dist_to_support != null ? sr.dist_to_support * 100 : pctDist(price, support);

  const edgeScore = bot.debug_state?.last_edge_score ?? bot.last_edge ?? 0;
  const requiredEdge = bot.edge_threshold ?? 3;
  const skipReason =
    bot.debug_state?.skip_reason ??
    bot.debug_state?.last_block_reason ??
    bot.execution_reason ??
    'Monitoring';

  const aiDecision = bot.last_ai?.decision ?? 'NO_TRADE';
  const aiDirection = bot.last_ai?.direction ?? 'NO_TRADE';
  const positions = bot.positions ?? [];
  const openPos = positions[0];
  const currentPosition =
    positions.length === 0 ? 'NONE' : (openPos?.dir ?? openPos?.side ?? 'OPEN').toUpperCase();

  let currentAction = 'WAITING';
  if (bot.execution_paused) currentAction = 'PAUSED';
  else if (positions.length > 0) currentAction = 'IN TRADE';
  else if ((bot.orders?.length ?? 0) > 0) currentAction = 'ORDER PENDING';
  else if (aiDecision === 'REJECT' || aiDirection === 'NO_TRADE') currentAction = 'WAITING';

  const srState = sr.sr_state ?? 'UNKNOWN';
  const marketLabel =
    srState === 'RANGE_COMPRESSION'
      ? 'Range Compression'
      : srState.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

  const mc = bot.market_context ?? {};
  const structureNote = mc.market_structure?.structure_bias ?? sr.sr_bias ?? regimeLabel(bot.regime);
  const mtf = mc.multi_tf?.agreement ?? 'MIXED';
  const adx = mc.trend_strength?.adx;

  const aiReasoning =
    bot.last_ai?.comment?.slice(0, 500) ??
    bot.last_ai?.reason?.slice(0, 500) ??
    `Edge ${edgeScore}/${requiredEdge}. ${skipReason !== 'Monitoring' ? skipReason : 'Pipeline active.'}`;

  const conclusion =
    edgeScore < requiredEdge || aiDecision === 'REJECT' || aiDirection === 'NO_TRADE'
      ? `No edge detected (${edgeScore}/${requiredEdge}). Waiting.`
      : `Signal active: ${aiDirection} @ ${bot.last_ai?.win_prob ?? 0}% win prob.`;

  const balance = bot.account_balance ?? STARTING_BALANCE;
  const equity = bot.equity ?? balance;
  const totalPnlPct = ((equity - STARTING_BALANCE) / STARTING_BALANCE) * 100;
  const dailyPnl = bot.daily_pnl_usd ?? 0;
  const dailyPnlPct = (dailyPnl / STARTING_BALANCE) * 100;

  const wsHealth =
    bot.diag?.ws_status === 'OK' || bot.ws_ready
      ? 'HEALTHY'
      : bot.execution_paused
        ? 'DEGRADED'
        : 'STALE';

  return {
    currentPrice: price,
    regime: bot.regime ?? 'UNKNOWN',
    support,
    resistance,
    distanceToResistancePct: Number(distResPct.toFixed(2)),
    distanceToSupportPct: Number(distSupPct.toFixed(2)),
    currentPosition,
    currentAction,
    aiDecision: aiDirection === 'NO_TRADE' ? 'NO_TRADE' : `${aiDecision} ${aiDirection}`.trim(),
    aiWinProbability: bot.last_ai?.win_prob ?? 0,
    currentEdge: Math.round(Number(edgeScore) * 10) / 10,
    requiredEdge,
    noTradeReason: skipReason,
    currentThinking: {
      market: marketLabel,
      support,
      resistance,
      distanceToResistancePct: Number(distResPct.toFixed(2)),
      distanceToSupportPct: Number(distSupPct.toFixed(2)),
      conclusion,
    },
    transparency: {
      currentEdge: Math.round(Number(edgeScore) * 10) / 10,
      requiredEdge,
      currentState: currentPosition === 'NONE' ? 'No Trade' : currentPosition,
      reason: skipReason,
    },
    openTrades: positions.map((p) => ({
      side: (p.dir ?? p.side ?? 'LONG').toUpperCase(),
      entryPrice: p.entry ?? 0,
      sizeUsd: (p.entry ?? 0) * (p.qty ?? 0),
      unrealizedPct: p.pnl_pct_margin ?? 0,
    })),
    pendingOrders: (bot.orders ?? []).map((o) => ({
      side: (o.side ?? 'buy').toUpperCase(),
      triggerPrice: o.limit_price ?? o.signal_price ?? 0,
      sizeUsd: (o.limit_price ?? 0) * (o.qty ?? 0),
    })),
    recentTrades: (bot.trades ?? [])
      .slice(-8)
      .reverse()
      .map((t) => ({
        side: (t.final_direction ?? t.dir ?? 'LONG').toUpperCase(),
        entryPrice: t.entry ?? 0,
        exitPrice: t.exit ?? 0,
        profitPct: t.pnl ?? 0,
        closedAt: t.ts ?? new Date().toISOString(),
      })),
    marketStructure: `${structureNote} · MTF ${mtf}${adx != null ? ` · ADX ${adx}` : ''}`,
    aiReasoning,
    riskStatus: bot.execution_paused ? 'PAUSED' : 'NORMAL',
    fundingStatus: bot.funding?.interpretation ?? bot.funding?.source ?? 'Bitfinex sim',
    dataSource: bot.data_source ?? bot.price_source ?? 'Bybit WS',
    wsHealth,
    dataQuality:
      (bot.data_quality ?? 0) >= 0.7 ? 'GOOD' : (bot.data_quality ?? 0) >= 0.5 ? 'FAIR' : 'LOW',
    pnl: {
      daily: Number(dailyPnlPct.toFixed(2)),
      total: Number(totalPnlPct.toFixed(2)),
    },
  };
}

/** Public-safe dashboard — no raw AI prompts, feature snapshots, or pipeline internals. */
export function mapBotStateToPublicDashboard(bot: BotApiState): TradingAgentDashboardState {
  const dash = mapBotStateToDashboard(bot);
  const mc = bot.market_context ?? {};
  const sr = bot.support_resistance ?? {};
  const bias = mc.market_structure?.structure_bias ?? sr.sr_bias ?? 'neutral';
  const mtf = mc.multi_tf?.agreement ?? 'mixed';
  const winProb = bot.last_ai?.win_prob ?? 0;
  const edge = dash.currentEdge;
  const req = dash.requiredEdge;

  dash.aiReasoning =
    dash.currentAction === 'PAUSED'
      ? 'Agent paused by operator. No new trades until resumed.'
      : dash.currentPosition !== 'NONE'
        ? `Managing open ${dash.currentPosition} position. Trend bias: ${bias}. Multi-timeframe: ${mtf}.`
        : edge < req
          ? `Watching market — edge ${edge}/${req} below threshold. Bias: ${bias}. Waiting for confirmation.`
          : `Signal under review — ${winProb}% confidence, ${bias} bias, ${mtf} alignment.`;

  dash.currentThinking = {
    ...dash.currentThinking,
    conclusion: dash.aiReasoning,
  };

  return dash;
}

export function sanitizeActivityForPublic(
  items: BotActivityEntry[],
): BotActivityEntry[] {
  return items.map((item) => {
    if (item.type.startsWith('AI_')) {
      return {
        ...item,
        reason: item.outcome
          ? `Market assessment: ${item.outcome}. Edge ${item.edgeScore ?? '—'}/${item.edgeRequired ?? '—'}.`
          : 'Market conditions did not meet entry criteria.',
      };
    }
    return item;
  });
}

function regimeLabel(regime?: string): string {
  if (!regime) return 'Unknown';
  return regime.charAt(0) + regime.slice(1).toLowerCase();
}

export function mapBotStateToAgentStats(bot: BotApiState, startingBalance = STARTING_BALANCE) {
  const sessionStart = bot.bot_start_time ?? 0;
  const rawTrades = bot.trades ?? [];
  const trades =
    sessionStart > 0
      ? rawTrades.filter((t) => {
          const ts = (t as { created_ts_ts?: number; entry_ts?: number }).created_ts_ts
            ?? (t as { entry_ts?: number }).entry_ts
            ?? 0;
          return Number(ts) >= sessionStart - 1;
        })
      : rawTrades;

  const balance = bot.account_balance ?? startingBalance;
  const openUnreal = (bot.positions ?? []).reduce(
    (sum, p) => sum + Number(p.unreal_usd ?? 0),
    0,
  );
  const equity = balance + openUnreal;

  const wins = trades.filter((t) => (t.net_pnl_usd ?? 0) > 0 || (t.pnl ?? 0) > 0).length;
  const winRate = trades.length > 0 ? (wins / trades.length) * 100 : 0;
  const netReturnPct = ((equity - startingBalance) / startingBalance) * 100;
  const liveSinceDays = bot.bot_start_time
    ? Math.max(1, Math.floor((Date.now() / 1000 - bot.bot_start_time) / 86400))
    : undefined;

  const openCount = bot.positions?.length ?? 0;
  let currentAction = 'WAITING';
  if (bot.execution_paused) currentAction = 'PAUSED';
  else if (openCount > 0) currentAction = 'IN TRADE';
  else if ((bot.orders?.length ?? 0) > 0) currentAction = 'ORDER PENDING';

  return {
    balanceUsd: balance,
    equityUsd: Number(equity.toFixed(2)),
    netReturnPct: Number(netReturnPct.toFixed(2)),
    tradeCount: bot.trade_count_session ?? trades.length,
    winRatePct: Number(winRate.toFixed(1)),
    liveSinceDays,
    currentPosition: openCount === 0 ? 'NONE' : (bot.positions?.[0]?.dir ?? 'OPEN'),
    currentAction,
    status: bot.strategy_mode === 'RESEARCH' ? 'TESTING' : bot.execution_paused ? 'PAUSED' : 'TESTING',
  };
}

export type BotActivityEntry = {
  id: string;
  type: string;
  title: string;
  reason: string | null;
  outcome: string | null;
  profitPct: number | null;
  edgeScore: number | null;
  edgeRequired: number | null;
  marketRegime: string | null;
  shareText: string | null;
  createdAt: string;
  entryPrice?: number | null;
  exitPrice?: number | null;
  balanceUsd?: number | null;
  netPnlUsd?: number | null;
};

/** Public trade journey — executed trades only (no NO_TRADE / AI reject noise). */
export function mapBotStateToExecutedTradesActivity(
  bot: BotApiState,
  _agentName: string,
): BotActivityEntry[] {
  const trades = bot.trades ?? [];
  let balance = STARTING_BALANCE;
  const chronological = trades.slice(-20).map((t) => {
    const netUsd =
      t.net_pnl_usd ??
      (t.pnl != null ? (Number(t.pnl) / 100) * STARTING_BALANCE : 0);
    balance += netUsd;
    const dir = (t.final_direction ?? t.dir ?? 'TRADE').toUpperCase();
    const profitPct = t.pnl ?? null;
    const won = (profitPct ?? 0) >= 0;
    return {
      id: `trade-${t.trade_id ?? t.ts}`,
      type: 'POSITION_CLOSED',
      title: `${dir} · ${won ? 'Win' : 'Loss'}`,
      reason: t.exit_reason ?? null,
      outcome: won ? 'Profit' : 'Loss',
      profitPct,
      edgeScore: null,
      edgeRequired: null,
      marketRegime: bot.support_resistance?.sr_state ?? bot.regime ?? null,
      shareText: null,
      createdAt: t.ts ?? new Date().toISOString(),
      entryPrice: t.entry ?? null,
      exitPrice: t.exit ?? null,
      balanceUsd: Number(balance.toFixed(2)),
      netPnlUsd: Number(netUsd.toFixed(2)),
    } satisfies BotActivityEntry;
  });

  return chronological.reverse();
}

export function filterActivityToExecutedTrades(items: BotActivityEntry[]): BotActivityEntry[] {
  return items.filter((item) => {
    const t = item.type.toUpperCase();
    const title = item.title.toUpperCase();
    if (t === 'NO_TRADE' || t === 'AI_REJECTED' || t === 'AI_APPROVED') return false;
    if (title.includes('NO TRADE') || title.includes('AI REJECTED') || title.includes('REJECTED')) {
      return false;
    }
    return (
      t.includes('TRADE') ||
      t.includes('POSITION') ||
      t.includes('OPEN') ||
      t.includes('CLOSE') ||
      t.includes('EXIT') ||
      item.entryPrice != null ||
      item.profitPct != null
    );
  });
}

export function mapBotStateToActivity(bot: BotApiState, agentName: string): BotActivityEntry[] {
  const items: BotActivityEntry[] = [];
  const edgeRequired = bot.edge_threshold ?? 3;
  const regime = bot.support_resistance?.sr_state ?? bot.regime ?? null;

  for (const t of (bot.trades ?? []).slice(-15).reverse()) {
    items.push({
      id: `trade-${t.trade_id ?? t.ts}`,
      type: 'POSITION_CLOSED',
      title: `${(t.final_direction ?? t.dir ?? 'Trade').toUpperCase()} closed`,
      reason: t.exit_reason ?? null,
      outcome: (t.pnl ?? 0) >= 0 ? 'Profit' : 'Loss',
      profitPct: t.pnl ?? null,
      edgeScore: null,
      edgeRequired: null,
      marketRegime: regime,
      shareText: null,
      createdAt: t.ts ?? new Date().toISOString(),
    });
  }

  for (const h of (bot.ai_history ?? []).slice().reverse()) {
    const decision = h.decision ?? 'UNKNOWN';
    items.push({
      id: `ai-${h.trade_id ?? h.time}`,
      type: decision === 'APPROVE' ? 'AI_APPROVED' : 'AI_REJECTED',
      title: decision === 'APPROVE' ? 'AI Approved' : 'AI Rejected',
      reason: h.comment?.slice(0, 200) ?? null,
      outcome: h.final_direction ?? h.ai_direction_raw ?? null,
      profitPct: null,
      edgeScore: bot.debug_state?.last_edge_score ?? null,
      edgeRequired,
      marketRegime: regime,
      shareText: null,
      createdAt: h.time ?? new Date().toISOString(),
    });
  }

  const skip = bot.debug_state?.skip_reason ?? bot.debug_state?.last_block_reason;
  if (skip) {
    items.unshift({
      id: `skip-${Date.now()}`,
      type: 'NO_TRADE',
      title: 'No Trade',
      reason: skip,
      outcome: 'Rejected',
      profitPct: null,
      edgeScore: bot.debug_state?.last_edge_score ?? bot.last_edge ?? 0,
      edgeRequired,
      marketRegime: regime,
      shareText: null,
      createdAt: new Date().toISOString(),
    });
  }

  return items.slice(0, 30);
}
