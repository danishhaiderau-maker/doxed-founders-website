import {
  EXCHANGE_PROVIDER_LABELS,
  type ExchangeProvider,
} from './trading-agent-adapters';

export type ExchangeCredentialPayload = {
  apiKey: string;
  apiSecret: string;
  passphrase?: string;
  testnet?: boolean;
};

export type ExchangeCredentialFieldConfig = {
  apiKeyLabel: string;
  apiSecretLabel: string;
  passphraseLabel?: string;
  passphraseRequired?: boolean;
  apiKeyPlaceholder?: string;
  apiSecretPlaceholder?: string;
  passphrasePlaceholder?: string;
  helpText?: string;
};

export const EXCHANGE_CREDENTIAL_CONFIG: Record<ExchangeProvider, ExchangeCredentialFieldConfig> = {
  bybit: {
    apiKeyLabel: 'API Key',
    apiSecretLabel: 'API Secret',
  },
  binance: {
    apiKeyLabel: 'API Key',
    apiSecretLabel: 'API Secret',
    helpText: 'Enable reading + spot/futures trading permissions. IP whitelist optional.',
  },
  okx: {
    apiKeyLabel: 'API Key',
    apiSecretLabel: 'Secret Key',
    passphraseLabel: 'Passphrase',
    passphraseRequired: true,
    passphrasePlaceholder: 'Set when you created the API key',
  },
  bitfinex: {
    apiKeyLabel: 'API Key',
    apiSecretLabel: 'API Secret',
    helpText: 'Bitfinex recommended: zero fees for eligible users. Enable Read + Trade only — never Withdraw.',
  },
  hyperliquid: {
    apiKeyLabel: 'Agent wallet address',
    apiSecretLabel: 'Agent private key',
    apiKeyPlaceholder: '0x…',
    apiSecretPlaceholder: 'Private key from Hyperliquid API page',
    passphraseLabel: 'Main wallet address (optional)',
    passphrasePlaceholder: 'Use if trading via vault/sub-account',
    helpText: 'Create an API wallet on Hyperliquid → API. Never use your main wallet private key.',
  },
};

export function exchangeRequiresPassphrase(provider: ExchangeProvider): boolean {
  return Boolean(EXCHANGE_CREDENTIAL_CONFIG[provider].passphraseRequired);
}

export function exchangeBotRuntimeNote(provider: ExchangeProvider): string | null {
  if (provider === 'bybit') return null;
  return `Credentials validated and stored. Showcase bot v0.5.0 still executes on Bybit — ${EXCHANGE_PROVIDER_LABELS[provider]} is ready for user hire connections; multi-exchange showcase trading ships in the next bot release.`;
}

/** Maps stored credentials to bot service env vars (Railway). */
export function exchangeCredentialsToEnvVars(
  provider: ExchangeProvider,
  creds: ExchangeCredentialPayload,
): Record<string, string> {
  const vars: Record<string, string> = { EXCHANGE_PROVIDER: provider };

  switch (provider) {
    case 'bybit':
      vars.BYBIT_API_KEY = creds.apiKey;
      vars.BYBIT_SECRET = creds.apiSecret;
      if (creds.testnet) vars.BYBIT_TESTNET = '1';
      break;
    case 'binance':
      vars.BINANCE_API_KEY = creds.apiKey;
      vars.BINANCE_API_SECRET = creds.apiSecret;
      if (creds.testnet) vars.BINANCE_TESTNET = '1';
      break;
    case 'okx':
      vars.OKX_API_KEY = creds.apiKey;
      vars.OKX_API_SECRET = creds.apiSecret;
      vars.OKX_PASSPHRASE = creds.passphrase ?? '';
      if (creds.testnet) vars.OKX_TESTNET = '1';
      break;
    case 'bitfinex':
      vars.BITFINEX_API_KEY = creds.apiKey;
      vars.BITFINEX_API_SECRET = creds.apiSecret;
      break;
    case 'hyperliquid':
      vars.HYPERLIQUID_WALLET_ADDRESS = creds.apiKey;
      vars.HYPERLIQUID_PRIVATE_KEY = creds.apiSecret;
      if (creds.passphrase?.trim()) vars.HYPERLIQUID_MAIN_WALLET = creds.passphrase.trim();
      break;
    default:
      break;
  }

  return vars;
}
