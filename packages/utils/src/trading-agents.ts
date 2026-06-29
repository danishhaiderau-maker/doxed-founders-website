export type AgentLiveSignalRow = {
  time: string;
  direction: string;
  confidence: number;
  regime: string;
  strategy: string;
  trigger: string;
  pullRequiredPct: number;
  signalPrice: number;
  maxPullPct: number;
  outcome: string;
  fillPrice: number | null;
  exitReason: string | null;
};

export type AgentLivePositionRow = {
  leg: string;
  side: string;
  qty: number;
  entry: number;
  current: number;
  stopLoss: number;
  takeProfit: number;
  pnlUsd: number;
};

export type AgentLivePendingOrderRow = {
  ageMin: number;
  side: string;
  status: string;
  qty: number;
  limitPrice: number;
  signalPrice: number;
};

export type AgentLiveExpiredOrderRow = {
  time: string;
  direction: string;
  limitPrice: number;
  ageMin: number;
  reason: string;
  confidence: number;
  mode: string;
};

export type AgentLiveTradeRow = {
  time: string;
  tradeId: string;
  direction: string;
  entry: number;
  exit: number;
  durationMin: number;
  pnlPct: number;
  netUsd: number;
  grossUsd: number;
  tradeFeesUsd: number;
  fundingUsd: number;
  aiBand: string;
};

export type TradingAgentDashboardState = {
  currentPrice: number;
  regime: string;
  support: number;
  resistance: number;
  distanceToResistancePct: number;
  distanceToSupportPct: number;
  currentPosition: string;
  currentAction: string;
  aiDecision: string;
  aiWinProbability: number;
  currentEdge: number;
  requiredEdge: number;
  noTradeReason: string;
  currentThinking: {
    market: string;
    support: number;
    resistance: number;
    distanceToResistancePct: number;
    distanceToSupportPct: number;
    conclusion: string;
  };
  transparency: {
    currentEdge: number;
    requiredEdge: number;
    currentState: string;
    reason: string;
  };
  openTrades: Array<{
    side: string;
    entryPrice: number;
    sizeUsd: number;
    unrealizedPct: number;
  }>;
  pendingOrders: Array<{
    side: string;
    triggerPrice: number;
    sizeUsd: number;
  }>;
  recentTrades: Array<{
    side: string;
    entryPrice: number;
    exitPrice: number;
    profitPct: number;
    closedAt: string;
  }>;
  marketStructure: string;
  aiReasoning: string;
  /** Most recent AI pipeline output — decision, comment, and block reason. */
  latestAiVerdict?: {
    decision: string;
    direction: string;
    winProbability: number;
    reason: string;
    comment: string;
    blockReason: string | null;
    edgeScore: number;
    requiredEdge: number;
    marketRegime: string;
    updatedAt: string | null;
  };
  riskStatus: string;
  fundingStatus: string;
  dataSource: string;
  wsHealth: string;
  dataQuality: string;
  pnl: { daily: number; total: number };
  /** Showcase / copy leverage (Bitfinex derivatives, typically 100x). */
  leverage: number;
  liveBook: {
    activeSignals: AgentLiveSignalRow[];
    positions: AgentLivePositionRow[];
    pendingOrders: AgentLivePendingOrderRow[];
    expiredOrders: AgentLiveExpiredOrderRow[];
    trades: AgentLiveTradeRow[];
  };
  /** Single-source-of-truth integrity block emitted by the showcase bot snapshot.
   * Present when the dashboard was built from a live bot `/api/state` or
   * `/api/relay-state` payload. Absent for pure DB-fallback dashboards. */
  stateIntegrity?: BotStateIntegrity;
  /** "Data from last N hours" window label, derived from the bot session start. */
  dataWindowHours?: number;
  /** Seconds the bot process has been running (uptime). */
  botUptimeHours?: number;
  /** Where this dashboard snapshot came from: 'live_bot' | 'railway_cache' | 'db_fallback'. */
  snapshotSource?: string;
};

/** State Integrity block — proves a downstream viewer is reading the same live bot. */
export type BotStateIntegrity = {
  snapshot_seq: number;
  snapshot_ts: string;
  snapshot_age_sec: number;
  bot_version: string;
  exchange: string;
  symbol: string;
  ws_connected: boolean;
  ws_status: string;
  ws_connected_sec_ago: number | null;
  rest_healthy: boolean;
  price_age_sec: number | null;
  book_age_sec: number | null;
  orders_synced: boolean;
  positions_synced: boolean;
  trades_synced: boolean;
  last_fill_sec_ago: number | null;
  execution_paused: boolean;
  live_armed: boolean;
  bitfinex_live_enabled: boolean;
  bitfinex_live?: Record<string, unknown> | null;
  genome_recorder: string;
  genome_bus_seq?: number | null;
  genome_stats?: Record<string, unknown> | null;
  research_db: boolean;
  relay_push?: {
    configured: boolean;
    url?: string | null;
    seq?: number;
    last_event?: string | null;
    last_ok?: boolean | null;
    last_sec_ago?: number | null;
  };
  tunnel_url?: string | null;
  analyzer_url?: string | null;
  last_fresh_reset_ts?: number | string | null;
};

export const TRADING_AGENT_KIND_LABELS: Record<string, string> = {
  TRADING: 'Trading Agents',
  RESEARCH: 'Research Agents',
  FOUNDER: 'Founder Agents',
  SCOUT: 'Scout Agents',
};

export const TRADING_AGENT_STATUS_LABELS: Record<string, string> = {
  TESTING: 'Testing',
  LIVE: 'Live',
  PAUSED: 'Paused',
  RETIRED: 'Retired',
};

export function buildTradingAgentActionShareText(input: {
  agentName: string;
  action: string;
  reason?: string | null;
  edgeScore?: number | null;
  edgeRequired?: number | null;
  marketRegime?: string | null;
  hubUrl?: string;
}): string {
  const lines = [`🤖 ${input.agentName}`, input.action];
  if (input.reason?.trim()) lines.push(`Reason: ${input.reason.trim()}`);
  if (input.edgeScore != null && input.edgeRequired != null) {
    lines.push(`Edge Score: ${input.edgeScore}/${input.edgeRequired}`);
  }
  if (input.marketRegime?.trim()) lines.push(`Market: ${input.marketRegime.trim()}`);
  lines.push('', 'Watch transparent AI trading live on Doxxed Crypto 👇');
  lines.push('#AgentHub #Crypto @DoxxedCrypto');
  if (input.hubUrl) lines.push(input.hubUrl);
  return lines.join('\n');
}

export function buildTradingAgentFollowShareText(input: {
  agentName: string;
  netReturnPct: number;
  winRatePct: number;
  hubUrl?: string;
}): string {
  const sign = input.netReturnPct >= 0 ? '+' : '';
  return [
    `👀 Following ${input.agentName} on Doxxed Crypto`,
    `Return ${sign}${input.netReturnPct.toFixed(1)}% · Win rate ${input.winRatePct.toFixed(0)}%`,
    'Transparent AI trader — see every decision in real time.',
    '#AgentHub @DoxxedCrypto',
    input.hubUrl ?? '',
  ]
    .filter(Boolean)
    .join('\n');
}

export type AgentShowcaseFlashTone = 'new-session' | 'live-testing' | 'offline';

export type AgentShowcaseFlash = {
  botVersion: string | null;
  sessionStartedAt: string | null;
  freshCollectionMode: boolean;
  tradeCountSession: number;
  tone: AgentShowcaseFlashTone;
  headline: string;
  body: string;
};

function formatShowcaseSessionDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
      timeZoneName: 'short',
    });
  } catch {
    return iso;
  }
}

/** Public copy when admin pushes a new bot build and restarts the showcase runway. */
export function buildAgentShowcaseFlash(input: {
  botVersion?: string | null;
  botStartTime?: number | null;
  freshCollectionMode?: boolean;
  tradeCountSession?: number;
  botConnected?: boolean;
  executionPaused?: boolean;
}): AgentShowcaseFlash | null {
  const botVersion = input.botVersion?.trim() || null;
  const sessionStartedAt =
    input.botStartTime && input.botStartTime > 0
      ? new Date(input.botStartTime * 1000).toISOString()
      : null;
  const freshCollectionMode = Boolean(input.freshCollectionMode);
  const tradeCountSession = Math.max(0, input.tradeCountSession ?? 0);
  const botConnected = Boolean(input.botConnected);
  const executionPaused = Boolean(input.executionPaused);

  if (!botConnected) {
    return {
      botVersion,
      sessionStartedAt,
      freshCollectionMode,
      tradeCountSession,
      tone: 'offline',
      headline: 'Showcase reconnecting',
      body: botVersion
        ? `The admin research bot (${botVersion}) is redeploying after a GitHub push. Stats refresh when the live session reconnects.`
        : 'The admin research bot is redeploying. Stats refresh when the live session reconnects.',
    };
  }

  const sessionAgeHours =
    input.botStartTime && input.botStartTime > 0
      ? (Date.now() / 1000 - input.botStartTime) / 3600
      : null;
  const isNewSession =
    freshCollectionMode || (sessionAgeHours != null && sessionAgeHours <= 168);
  const versionLabel = botVersion ?? 'latest build';
  const startedLabel = sessionStartedAt
    ? formatShowcaseSessionDate(sessionStartedAt)
    : 'this session';

  if (isNewSession) {
    const tradeHint =
      tradeCountSession > 0
        ? `${tradeCountSession} trade${tradeCountSession === 1 ? '' : 's'} logged so far in this run.`
        : 'Trading resumed — waiting for the first setup that clears edge.';

    return {
      botVersion,
      sessionStartedAt,
      freshCollectionMode,
      tradeCountSession,
      tone: 'new-session',
      headline: freshCollectionMode
        ? 'Fresh research run — new GitHub version deployed'
        : 'New showcase session — strategy under live test',
      body: executionPaused
        ? `@bitbro4crypto pushed ${versionLabel} from GitHub and reset the admin showcase to a clean $500 paper runway (${startedLabel}). Execution is paused briefly — stats below are from this test window only.`
        : `@bitbro4crypto pushed ${versionLabel} from GitHub and reset the admin showcase to a clean $500 paper runway (${startedLabel}). The bot is actively trading again to prove how profitable this BTC strategy is in current conditions. ${tradeHint}`,
    };
  }

  return {
    botVersion,
    sessionStartedAt,
    freshCollectionMode,
    tradeCountSession,
    tone: 'live-testing',
    headline: 'Live strategy research — admin is testing in public',
    body: `Showcase running ${versionLabel} since ${startedLabel}. Each GitHub deploy can reset the $500 runway so you see clean, honest performance — not cherry-picked history. ${tradeCountSession} trade${tradeCountSession === 1 ? '' : 's'} in the current window.`,
  };
}
