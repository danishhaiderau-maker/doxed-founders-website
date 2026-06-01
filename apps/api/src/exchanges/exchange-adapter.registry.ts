import type { ExchangeProvider } from '@dcf/utils';
import type { ExchangeAdapter, ExchangeCredentials, ExchangeValidationResult } from './exchange-adapter.interface';
import { BybitExchangeAdapter } from './bybit.adapter';

class StubExchangeAdapter implements ExchangeAdapter {
  constructor(readonly provider: ExchangeProvider) {}

  async validateCredentials(_creds: ExchangeCredentials): Promise<ExchangeValidationResult> {
    return {
      ok: false,
      message: `${this.provider} adapter coming soon — use Bybit for now`,
    };
  }
}

const STUBS: ExchangeProvider[] = ['hyperliquid', 'bitfinex', 'binance', 'okx'];

export class ExchangeAdapterRegistry {
  private readonly adapters = new Map<ExchangeProvider, ExchangeAdapter>();

  constructor() {
    this.adapters.set('bybit', new BybitExchangeAdapter());
    for (const p of STUBS) {
      this.adapters.set(p, new StubExchangeAdapter(p));
    }
  }

  get(provider: string): ExchangeAdapter | null {
    return this.adapters.get(provider as ExchangeProvider) ?? null;
  }

  listProviders(): ExchangeProvider[] {
    return [...this.adapters.keys()];
  }
}
