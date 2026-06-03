import { Wallet, getAddress, isAddress } from 'ethers';
import type { ExchangeAdapter, ExchangeCredentials, ExchangeValidationResult } from './exchange-adapter.interface';
import { exchangeErrorMessage, exchangeFetch } from './exchange-http.util';

function normalizePrivateKey(secret: string): string {
  const trimmed = secret.trim();
  return trimmed.startsWith('0x') ? trimmed : `0x${trimmed}`;
}

/** Validates Hyperliquid agent wallet via address + optional EIP-712 key check. */
export class HyperliquidExchangeAdapter implements ExchangeAdapter {
  readonly provider = 'hyperliquid' as const;

  async validateCredentials(creds: ExchangeCredentials): Promise<ExchangeValidationResult> {
    const walletAddress = creds.apiKey.trim();
    if (!isAddress(walletAddress)) {
      return { ok: false, message: 'Invalid Hyperliquid wallet address (expected 0x…)' };
    }

    let derivedAddress: string;
    try {
      derivedAddress = getAddress(new Wallet(normalizePrivateKey(creds.apiSecret)).address);
    } catch {
      return { ok: false, message: 'Invalid agent private key' };
    }

    if (derivedAddress.toLowerCase() !== getAddress(walletAddress).toLowerCase()) {
      return {
        ok: false,
        message: 'Private key does not match the agent wallet address',
      };
    }

    const queryUser = creds.passphrase?.trim()
      ? getAddress(creds.passphrase.trim())
      : derivedAddress;

    try {
      const res = await exchangeFetch('https://api.hyperliquid.xyz/info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'clearinghouseState', user: queryUser }),
      });
      const body = (await res.json()) as {
        marginSummary?: { accountValue?: string };
        assetPositions?: unknown[];
      };
      if (!res.ok) {
        return { ok: false, message: `Hyperliquid error ${res.status}` };
      }
      const accountValue = body.marginSummary?.accountValue;
      return {
        ok: true,
        message: 'Hyperliquid agent wallet connected',
        accountLabel: accountValue ? `Account value ${accountValue} USD` : queryUser,
        permissions: ['read', 'trade'],
      };
    } catch (err) {
      return { ok: false, message: exchangeErrorMessage(err, 'Hyperliquid') };
    }
  }
}
