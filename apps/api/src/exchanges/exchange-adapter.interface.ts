import type { ExchangeProvider } from '@dcf/utils';

export type ExchangeCredentials = {
  apiKey: string;
  apiSecret: string;
  passphrase?: string;
  testnet?: boolean;
};

export type ExchangeValidationResult = {
  ok: boolean;
  message: string;
  accountLabel?: string;
  permissions?: string[];
};

export interface ExchangeAdapter {
  readonly provider: ExchangeProvider;
  validateCredentials(creds: ExchangeCredentials): Promise<ExchangeValidationResult>;
}
