#!/usr/bin/env node
/**
 * Verify x402 signal intent returns 402 without payment (Step 1 health check).
 */
const API =
  process.env.API_URL?.replace(/\/$/, '') ||
  'https://doxed-founders-website-production.up.railway.app';

const intentUrl = `${API}/api/trading-agents/conservative-btc/signals/intent`;
const mandateUrl = `${API}/api/trading-agents/conservative-btc/signals/mandate`;

async function main() {
  console.log('\n=== x402 signal intent check ===\n');

  const mandateRes = await fetch(mandateUrl);
  const mandate = await mandateRes.json();
  const x402 = mandate.x402;
  console.log('Mandate x402.support:', x402?.support ?? false);
  if (x402?.pay_to_evm) console.log('pay_to_evm:', x402.pay_to_evm);
  if (x402?.price_label) console.log('price:', x402.price_label);

  const intentRes = await fetch(intentUrl, { redirect: 'manual' });
  console.log('\nGET /signals/intent (no key, no payment):', intentRes.status);

  if (intentRes.status === 402) {
    console.log('✓ x402 paywall active — agents must pay or use API key');
    const body = await intentRes.text();
    if (body.length < 800) console.log(body);
    process.exit(0);
  }

  if (intentRes.status === 401) {
    console.error('✗ Got 401 instead of 402 — x402 middleware not intercepting (redeploy API after fix).');
    process.exit(1);
  }

  console.warn(`Unexpected status ${intentRes.status}`);
  process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
