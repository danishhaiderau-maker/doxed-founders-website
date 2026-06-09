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
  riskStatus: string;
  fundingStatus: string;
  dataSource: string;
  wsHealth: string;
  dataQuality: string;
  pnl: { daily: number; total: number };
  liveBook: {
    activeSignals: AgentLiveSignalRow[];
    positions: AgentLivePositionRow[];
    pendingOrders: AgentLivePendingOrderRow[];
    expiredOrders: AgentLiveExpiredOrderRow[];
    trades: AgentLiveTradeRow[];
  };
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
