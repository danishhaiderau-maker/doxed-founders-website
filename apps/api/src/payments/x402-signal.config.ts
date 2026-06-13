export const X402_SIGNAL_INTENT_PRICE = process.env.X402_SIGNAL_INTENT_PRICE ?? '$0.10';
export const X402_SIGNAL_NETWORK = process.env.X402_SIGNAL_NETWORK ?? 'eip155:8453';
export const X402_CDP_FACILITATOR_URL = 'https://api.cdp.coinbase.com/platform/v2/x402';
export const X402_BAZAAR_CATALOG_URL = `${X402_CDP_FACILITATOR_URL}/discovery/resources`;
export const X402_BAZAAR_SEARCH_URL = `${X402_CDP_FACILITATOR_URL}/discovery/search`;
const X402_PUBLIC_FACILITATOR_URL = 'https://facilitator.x402.org';

type FacilitatorClientConfig = { url: string; createAuthHeaders?: () => Promise<unknown> };

export function resolveFacilitatorClientConfig(): FacilitatorClientConfig {
  const cdpId = process.env.CDP_API_KEY_ID?.trim();
  const cdpSecret = process.env.CDP_API_KEY_SECRET?.trim();
  const explicit = process.env.X402_FACILITATOR_URL?.trim();

  if (cdpId && cdpSecret) {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { createFacilitatorConfig } = require('@coinbase/x402');
    return createFacilitatorConfig(cdpId, cdpSecret);
  }

  if (explicit?.includes('cdp.coinbase.com')) {
    console.warn(
      '[x402] CDP facilitator URL configured but CDP_API_KEY_ID/SECRET missing — add keys to vault for Bazaar indexing.',
    );
  }

  return { url: explicit || X402_PUBLIC_FACILITATOR_URL };
}

export function resolveX402FacilitatorUrl(): string {
  return resolveFacilitatorClientConfig().url;
}

export function isX402BazaarEnabled(): boolean {
  const url = resolveX402FacilitatorUrl();
  return (
    url.includes('cdp.coinbase.com') &&
    Boolean(process.env.CDP_API_KEY_ID?.trim() && process.env.CDP_API_KEY_SECRET?.trim())
  );
}

export function isX402SignalIntentEnabled(payTo?: string | null): boolean {
  const addr = payTo?.trim() || process.env.X402_EVM_PAY_TO?.trim() || null;
  return process.env.X402_SIGNAL_ENABLED !== 'false' && Boolean(addr);
}

export function buildBazaarRouteExtensions(): Record<string, unknown> {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { declareDiscoveryExtension } = require('@x402/extensions/bazaar');
  return declareDiscoveryExtension({
    output: {
      example: {
        cycle: {
          cycleId: 'clsignalcycleexample',
          tradeId: 'btc-perp-001',
          status: 'INTENT',
          intent: {
            symbol: 'BTC',
            side: 'long',
            exchangeNeutral: true,
          },
          expiresAt: '2026-06-13T12:00:00.000Z',
          botVersion: '1.0.0',
          createdAt: '2026-06-13T11:00:00.000Z',
        },
        mandate: {
          x402: { support: true, price_label: '$0.10', network: 'eip155:8453' },
        },
      },
      schema: {
        type: 'object',
        properties: {
          cycle: {
            type: 'object',
            nullable: true,
            properties: {
              cycleId: { type: 'string' },
              tradeId: { type: 'string' },
              status: { type: 'string' },
              intent: { type: 'object' },
              expiresAt: { type: 'string', nullable: true },
              botVersion: { type: 'string', nullable: true },
              createdAt: { type: 'string' },
            },
          },
          mandate: { type: 'object' },
        },
      },
    },
  });
}
