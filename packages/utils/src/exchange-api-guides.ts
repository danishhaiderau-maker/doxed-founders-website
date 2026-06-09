import type { ExchangeProvider } from './trading-agent-adapters';
import { EXCHANGE_PROVIDER_LABELS } from './trading-agent-adapters';

export type ExchangeApiGuide = {
  provider: ExchangeProvider;
  recommended?: boolean;
  recommendReason?: string;
  steps: string[];
  requiredPermissions: string[];
  forbiddenPermissions: string[];
  docsUrl: string;
  maxCapitalWarningUsd: number;
  /** Short note on what credentials the hire wizard expects. */
  credentialHint?: string;
};

export const BITFINEX_RECOMMEND_BANNER =
  'Recommended: Bitfinex currently offers zero trading fees for eligible users — ideal for beta testing while fees would otherwise eat into small accounts.';

export const AGENT_BETA_RISK_COPY = {
  title: 'Experimental agent — beta phase',
  bullets: [
    'This strategy remains in active testing. Do not allocate more than $500.',
    'Past performance does not guarantee future results.',
    'The agent may lose capital. Use only funds you can afford to lose.',
    'High risk. Not financial advice.',
  ],
  checkboxLabel: 'I understand the risks and will not allocate more than $500.',
};

export const EXCHANGE_API_GUIDES: Record<ExchangeProvider, ExchangeApiGuide> = {
  bitfinex: {
    provider: 'bitfinex',
    recommended: true,
    recommendReason: BITFINEX_RECOMMEND_BANNER,
    docsUrl: 'https://support.bitfinex.com/hc/en-us/articles/115003363429-How-to-create-and-revoke-a-Bitfinex-API-Key',
    maxCapitalWarningUsd: 500,
    credentialHint: 'API Key + API Secret (no passphrase).',
    requiredPermissions: ['Read balance', 'Read orders', 'Create orders', 'Cancel orders'],
    forbiddenPermissions: ['Withdraw funds'],
    steps: [
      'Log in to Bitfinex and open Account → API Keys.',
      'Click Create New Key and label it (e.g. Doxxed Crypto Conservative BTC).',
      'Enable Read and Write permissions for Orders and Wallets — do NOT enable Withdraw.',
      'Optionally restrict by IP for extra security.',
      'Copy the API Key and API Secret immediately (secret shown once).',
      'Paste both into the Hire Agent wizard — platform mirrors admin AI signals on your account.',
    ],
  },
  bybit: {
    provider: 'bybit',
    docsUrl: 'https://www.bybit.com/en/help-center/article/How-to-create-your-API-key',
    maxCapitalWarningUsd: 500,
    credentialHint: 'API Key + API Secret. Use Unified Trading account keys for BTC perps.',
    requiredPermissions: ['Read', 'Trade (derivatives)'],
    forbiddenPermissions: ['Withdraw', 'Transfer'],
    steps: [
      'Log in to Bybit → Account & Security → API Management.',
      'Create a new API key (System-generated keys recommended).',
      'Enable Read-Write for Contract/Unified trading; disable Withdraw and Transfer.',
      'Complete 2FA verification and save Key + Secret securely.',
      'For testnet: create keys at testnet.bybit.com and check “Use testnet” in the wizard.',
      'Paste credentials into Hire Agent — only trading permissions are used.',
    ],
  },
  binance: {
    provider: 'binance',
    docsUrl: 'https://www.binance.com/en/support/faq/how-to-create-api-keys-on-binance-360002502072',
    maxCapitalWarningUsd: 500,
    credentialHint: 'API Key + Secret. Futures keys if copying BTC perpetuals.',
    requiredPermissions: ['Enable Reading', 'Enable Futures (or Spot & Margin if spot only)'],
    forbiddenPermissions: ['Enable Withdrawals'],
    steps: [
      'Binance → Profile → API Management → Create API.',
      'Label the key and complete security verification (2FA + email).',
      'Enable Reading + Futures trading (or Spot as needed); keep Withdrawals disabled.',
      'Restrict trusted IPs if possible — strongly recommended.',
      'Copy API Key and Secret — Secret is shown once.',
      'Paste into Hire Agent. Never share keys in chat or email.',
    ],
  },
  okx: {
    provider: 'okx',
    docsUrl: 'https://www.okx.com/help-center/how-to-create-an-api-key',
    maxCapitalWarningUsd: 500,
    credentialHint: 'API Key + Secret + Passphrase (OKX requires all three).',
    requiredPermissions: ['Read', 'Trade'],
    forbiddenPermissions: ['Withdraw'],
    steps: [
      'OKX → Profile → API → Create API key.',
      'Set a passphrase — you must enter this in the hire wizard (OKX requires it on every request).',
      'Enable Read + Trade permissions; disable Withdraw.',
      'Bind IP whitelist if your network is stable.',
      'Save Key, Secret, and Passphrase — Secret is shown once.',
      'Paste all three fields into Hire Agent.',
    ],
  },
  hyperliquid: {
    provider: 'hyperliquid',
    docsUrl: 'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets',
    maxCapitalWarningUsd: 500,
    credentialHint: 'Agent wallet address + agent wallet private key (not your main wallet).',
    requiredPermissions: ['Agent wallet trading'],
    forbiddenPermissions: ['Main wallet private key export'],
    steps: [
      'Open Hyperliquid → Settings → API.',
      'Create an API wallet (agent wallet) — never paste your main wallet private key.',
      'Fund the agent wallet with a small amount for gas/fees if required.',
      'Copy agent wallet address and private key.',
      'Paste address as API Key and private key as Secret in Hire Agent.',
      'Revoke the agent wallet anytime from Hyperliquid settings.',
    ],
  },
};

export function exchangeGuideLabel(provider: ExchangeProvider): string {
  return EXCHANGE_PROVIDER_LABELS[provider];
}
