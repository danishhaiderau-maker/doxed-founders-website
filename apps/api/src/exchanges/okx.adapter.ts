import { createHmac } from 'node:crypto';
import type { ExchangeAdapter, ExchangeCredentials, ExchangeValidationResult } from './exchange-adapter.interface';
import { exchangeErrorMessage, exchangeFetch } from './exchange-http.util';

function signOkx(secret: string, prehash: string): string {
  return createHmac('sha256', secret).update(prehash).digest('base64');
}

/** Validates OKX API keys via account balance (requires passphrase). */
export class OkxExchangeAdapter implements ExchangeAdapter {
  readonly provider = 'okx' as const;

  async validateCredentials(creds: ExchangeCredentials): Promise<ExchangeValidationResult> {
    if (!creds.passphrase?.trim()) {
      return { ok: false, message: 'OKX passphrase is required' };
    }

    const base = 'https://www.okx.com';
    const method = 'GET';
    const path = '/api/v5/account/balance';
    const timestamp = new Date().toISOString();
    const prehash = `${timestamp}${method}${path}`;
    const sign = signOkx(creds.apiSecret, prehash);

    try {
      const res = await exchangeFetch(`${base}${path}`, {
        headers: {
          'OK-ACCESS-KEY': creds.apiKey,
          'OK-ACCESS-SIGN': sign,
          'OK-ACCESS-TIMESTAMP': timestamp,
          'OK-ACCESS-PASSPHRASE': creds.passphrase.trim(),
          ...(creds.testnet ? { 'x-simulated-trading': '1' } : {}),
        },
      });
      const body = (await res.json()) as { code?: string; msg?: string; data?: unknown[] };
      if (body.code === '0') {
        return {
          ok: true,
          message: creds.testnet ? 'OKX demo trading API connected' : 'OKX API connected',
          accountLabel: 'Unified trading account',
          permissions: ['read', 'trade'],
        };
      }
      return { ok: false, message: body.msg ?? `OKX error ${body.code}` };
    } catch (err) {
      return { ok: false, message: exchangeErrorMessage(err, 'OKX') };
    }
  }
}
