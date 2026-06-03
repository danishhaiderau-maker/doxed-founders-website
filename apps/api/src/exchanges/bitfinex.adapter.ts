import { createHmac } from 'node:crypto';
import type { ExchangeAdapter, ExchangeCredentials, ExchangeValidationResult } from './exchange-adapter.interface';
import { exchangeErrorMessage, exchangeFetch } from './exchange-http.util';

function signBitfinex(secret: string, payload: string): string {
  return createHmac('sha384', secret).update(payload).digest('hex');
}

/** Validates Bitfinex API keys via authenticated wallet read. */
export class BitfinexExchangeAdapter implements ExchangeAdapter {
  readonly provider = 'bitfinex' as const;

  async validateCredentials(creds: ExchangeCredentials): Promise<ExchangeValidationResult> {
    const apiPath = 'v2/auth/r/wallets';
    const nonce = (Date.now() * 1000).toString();
    const body = JSON.stringify({});
    const payload = `/api/${apiPath}${nonce}${body}`;
    const signature = signBitfinex(creds.apiSecret, payload);

    try {
      const res = await exchangeFetch(`https://api.bitfinex.com/${apiPath}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'bfx-apikey': creds.apiKey,
          'bfx-nonce': nonce,
          'bfx-signature': signature,
        },
        body,
      });
      const text = await res.text();
      if (res.ok) {
        return {
          ok: true,
          message: 'Bitfinex API connected',
          accountLabel: 'Exchange wallet',
          permissions: ['read', 'trade'],
        };
      }
      let message = `Bitfinex error ${res.status}`;
      try {
        const parsed = JSON.parse(text) as [string, string] | { message?: string };
        if (Array.isArray(parsed) && parsed[1]) message = parsed[1];
        else if (parsed && typeof parsed === 'object' && 'message' in parsed && parsed.message) {
          message = parsed.message;
        }
      } catch {
        if (text.trim()) message = text.slice(0, 200);
      }
      return { ok: false, message };
    } catch (err) {
      return { ok: false, message: exchangeErrorMessage(err, 'Bitfinex') };
    }
  }
}
