import { createHmac } from 'node:crypto';
import type { ExchangeAdapter, ExchangeCredentials, ExchangeValidationResult } from './exchange-adapter.interface';

function signBybit(secret: string, payload: string): string {
  return createHmac('sha256', secret).update(payload).digest('hex');
}

/** Validates Bybit API keys via read-only wallet balance call. */
export class BybitExchangeAdapter implements ExchangeAdapter {
  readonly provider = 'bybit' as const;

  async validateCredentials(creds: ExchangeCredentials): Promise<ExchangeValidationResult> {
    const base = creds.testnet ? 'https://api-testnet.bybit.com' : 'https://api.bybit.com';
    const timestamp = Date.now().toString();
    const recvWindow = '5000';
    const query = 'accountType=UNIFIED';
    const preSign = `${timestamp}${creds.apiKey}${recvWindow}${query}`;
    const sign = signBybit(creds.apiSecret, preSign);

    try {
      const res = await fetch(`${base}/v5/account/wallet-balance?${query}`, {
        headers: {
          'X-BAPI-API-KEY': creds.apiKey,
          'X-BAPI-SIGN': sign,
          'X-BAPI-TIMESTAMP': timestamp,
          'X-BAPI-RECV-WINDOW': recvWindow,
        },
        signal: AbortSignal.timeout(8000),
      });
      const body = (await res.json()) as { retCode?: number; retMsg?: string };
      if (body.retCode === 0) {
        return {
          ok: true,
          message: 'Bybit API connected',
          accountLabel: 'Unified account',
          permissions: ['read', 'trade'],
        };
      }
      return { ok: false, message: body.retMsg ?? `Bybit error ${body.retCode}` };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, message: `Bybit unreachable: ${msg}` };
    }
  }
}
