/** Supported exchange providers for user agent instances (not hardcoded to Bybit). */
export const EXCHANGE_PROVIDERS = [
  'bybit',
  'hyperliquid',
  'bitfinex',
  'binance',
  'okx',
] as const;

export type ExchangeProvider = (typeof EXCHANGE_PROVIDERS)[number];

export const EXCHANGE_PROVIDER_LABELS: Record<ExchangeProvider, string> = {
  bybit: 'Bybit',
  hyperliquid: 'Hyperliquid',
  bitfinex: 'Bitfinex',
  binance: 'Binance',
  okx: 'OKX',
};

/** Supported AI providers for trading agents — platform provides AI by default; BYOK later. */
export const TRADING_AGENT_AI_PROVIDERS = [
  'deepseek',
  'openai',
  'claude',
  'gemini',
  'openrouter',
] as const;

export type TradingAgentAiProvider = (typeof TRADING_AGENT_AI_PROVIDERS)[number];

export const TRADING_AGENT_AI_PROVIDER_LABELS: Record<TradingAgentAiProvider, string> = {
  deepseek: 'DeepSeek',
  openai: 'OpenAI',
  claude: 'Claude',
  gemini: 'Gemini',
  openrouter: 'OpenRouter',
};

export type AgentInstanceStatus = 'PENDING' | 'ACTIVE' | 'PAUSED' | 'ERROR';

export function exchangeCredentialProvider(exchange: ExchangeProvider): string {
  return `exchange:${exchange}`;
}
