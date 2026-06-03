import { createHmac } from 'node:crypto';
import type { ExchangeAdapter, ExchangeCredentials, ExchangeValidationResult } from './exchange-adapter.interface';
import { exchangeErrorMessage, exchangeFetch } from './exchange-http.util';

function signBinance(secret: string, query: string): string {
  return createHmac('sha256', secret).update(query).digest('hex');
}

/** Validates Binance API keys via signed account snapshot. */
export class BinanceExchangeAdapter implements ExchangeAdapter {
  readonly provider = 'binance' as const;

  async validateCredentials(creds: ExchangeCredentials): Promise<ExchangeValidationResult> {
    const base = creds.testnet ? 'https://testnet.binance.vision' : 'https://api.binance.com';
    const timestamp = Date.now().toString();
    const query = `timestamp=${timestamp}`;
    const signature = signBinance(creds.apiSecret, query);

    try {
      const res = await exchangeFetch(
        `${base}/api/v3/account?${query}&signature=${signature}`,
        { headers: { 'X-MBX-APIKEY': creds.apiKey } },
      );
      const body = (await res.json()) as { code?: number; msg?: string; accountType?: string };
      if (res.ok && !body.code) {
        return {
          ok: true,
          message: 'Binance API connected',
          accountLabel: body.accountType ?? 'Spot account',
          permissions: ['read', 'trade'],
        };
      }
      return { ok: false, message: body.msg ?? `Binance error ${body.code ?? res.status}` };
    } catch (err) {
      return { ok: false, message: exchangeErrorMessage(err, 'Binance') };
    }
  }
}
