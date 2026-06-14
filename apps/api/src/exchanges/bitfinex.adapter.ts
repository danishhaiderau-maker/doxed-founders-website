import type { ExchangeAdapter, ExchangeCredentials, ExchangeValidationResult } from './exchange-adapter.interface';
import { BitfinexTradingClient } from './bitfinex-api.client';

/** Validates Bitfinex API keys via authenticated wallet read. */
export class BitfinexExchangeAdapter implements ExchangeAdapter {
  readonly provider = 'bitfinex' as const;
  private readonly client = new BitfinexTradingClient();

  async validateCredentials(creds: ExchangeCredentials): Promise<ExchangeValidationResult> {
    const result = await this.client.validateCredentials(creds);
    if (result.ok) {
      return {
        ok: true,
        message: result.message,
        accountLabel: 'Exchange wallet',
        permissions: ['read', 'trade'],
      };
    }
    return { ok: false, message: result.message };
  }
}
