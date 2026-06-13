#!/usr/bin/env node
/**
 * Verify x402 signal intent returns 402 with Bazaar metadata (Steps 1–2 health check).
 */
const API =
  process.env.API_URL?.replace(/\/$/, '') ||
  'https://doxed-founders-website-production.up.railway.app';

const intentUrl = `${API}/api/trading-agents/conservative-btc/signals/intent`;
const mandateUrl = `${API}/api/trading-agents/conservative-btc/signals/mandate`;
const BAZAAR_SEARCH =
  'https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?query=conservative+btc+signal';

async function main() {
  console.log('\n=== x402 signal intent check ===\n');

  const mandateRes = await fetch(mandateUrl);
  const mandate = await mandateRes.json();
  const x402 = mandate.x402;
  console.log('Mandate x402.support:', x402?.support ?? false);
  if (x402?.pay_to_evm) console.log('pay_to_evm:', x402.pay_to_evm);
  if (x402?.price_label) console.log('price:', x402.price_label);
  if (x402?.facilitator) console.log('facilitator:', x402.facilitator);
  if (x402?.bazaar) {
    console.log('bazaar.discoverable:', x402.bazaar.discoverable ?? false);
    if (x402.bazaar.catalog_url) console.log('bazaar.catalog:', x402.bazaar.catalog_url);
  }

  const intentRes = await fetch(intentUrl, { redirect: 'manual' });
  console.log('\nGET /signals/intent (no key, no payment):', intentRes.status);

  if (intentRes.status !== 402) {
    if (intentRes.status === 401) {
      console.error('✗ Got 401 instead of 402 — x402 middleware not intercepting (redeploy API after fix).');
    } else {
      console.warn(`Unexpected status ${intentRes.status}`);
    }
    process.exit(1);
  }

  console.log('✓ x402 paywall active — agents must pay or use API key');

  let body;
  try {
    body = await intentRes.json();
  } catch {
    console.error('✗ 402 response is not JSON');
    process.exit(1);
  }

  const bazaarExt = body.extensions?.bazaar;
  if (bazaarExt?.info?.output?.example) {
    console.log('✓ Bazaar extension present on 402 (discovery metadata ready)');
  } else {
    console.warn('⚠ Bazaar extension missing on 402 — redeploy Step 2 changes');
  }

  if (body.accepts?.[0]?.payTo) {
    console.log('accepts.payTo:', body.accepts[0].payTo);
  }

  if (x402?.bazaar?.discoverable) {
    console.log('\n=== CDP Bazaar search (may be empty until first settlement) ===\n');
    try {
      const searchRes = await fetch(BAZAAR_SEARCH);
      if (searchRes.ok) {
        const search = await searchRes.json();
        const hits = search.resources ?? search.items ?? [];
        const match = hits.find((r) =>
          String(r.resource ?? '').includes('conservative-btc/signals/intent'),
        );
        if (match) {
          console.log('✓ Listed in CDP Bazaar:', match.resource);
        } else {
          console.log(
            '○ Not indexed yet — complete one paid intent via CDP facilitator to auto-list',
          );
        }
      } else {
        console.log('Bazaar search HTTP', searchRes.status, '(non-fatal)');
      }
    } catch (e) {
      console.log('Bazaar search skipped:', e.message);
    }
  } else {
    console.log(
      '\n○ CDP Bazaar listing pending — add CDP_API_KEY_ID + CDP_API_KEY_SECRET to vault and sync:production',
    );
  }

  process.exit(0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
