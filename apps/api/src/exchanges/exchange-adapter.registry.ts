import type { ExchangeProvider } from '@dcf/utils';
import type { ExchangeAdapter } from './exchange-adapter.interface';
import { BinanceExchangeAdapter } from './binance.adapter';
import { BitfinexExchangeAdapter } from './bitfinex.adapter';
import { BybitExchangeAdapter } from './bybit.adapter';
import { HyperliquidExchangeAdapter } from './hyperliquid.adapter';
import { OkxExchangeAdapter } from './okx.adapter';

export class ExchangeAdapterRegistry {
  private readonly adapters = new Map<ExchangeProvider, ExchangeAdapter>();

  constructor() {
    this.adapters.set('bybit', new BybitExchangeAdapter());
    this.adapters.set('binance', new BinanceExchangeAdapter());
    this.adapters.set('okx', new OkxExchangeAdapter());
    this.adapters.set('bitfinex', new BitfinexExchangeAdapter());
    this.adapters.set('hyperliquid', new HyperliquidExchangeAdapter());
  }

  get(provider: string): ExchangeAdapter | null {
    return this.adapters.get(provider as ExchangeProvider) ?? null;
  }

  listProviders(): ExchangeProvider[] {
    return [...this.adapters.keys()];
  }

  isAvailable(provider: string): boolean {
    return this.adapters.has(provider as ExchangeProvider);
  }
}
