#!/usr/bin/env node
/**
 * Validates Conservative BTC Agent discovery metadata and prints registration steps.
 * On-chain mints require your Phantom (SAID) or Base wallet (Spawn) signature.
 *
 * Usage:
 *   node scripts/prepare-agent-registrations.mjs
 *   node scripts/prepare-agent-registrations.mjs --spawn-api-key $THESPAWN_API_KEY
 */

import {
  buildConservativeBtcAgentCard,
  buildConservativeBtcErc8004AgentJson,
  resolveConservativeBtcAgentUrls,
} from '../packages/utils/dist/index.js';

const site = process.env.NEXT_PUBLIC_SITE_URL?.trim() || 'https://doxxedcrypto.digital';
const api =
  process.env.NEXT_PUBLIC_API_URL?.trim() ||
  'https://doxed-founders-website-production.up.railway.app';

const urls = resolveConservativeBtcAgentUrls(site, api);
const spawnKey = process.argv.includes('--spawn-api-key')
  ? process.argv[process.argv.indexOf('--spawn-api-key') + 1]
  : process.env.THESPAWN_API_KEY?.trim();

async function checkUrl(label, url) {
  try {
    const res = await fetch(url, { headers: { Accept: 'application/json' } });
    const ok = res.ok;
    console.log(`${ok ? '✓' : '✗'} ${label}: ${url} (${res.status})`);
    return ok;
  } catch (err) {
    console.log(`✗ ${label}: ${url} (${err instanceof Error ? err.message : 'failed'})`);
    return false;
  }
}

async function main() {
  console.log('\n=== Conservative BTC Agent — registration prep ===\n');
  console.log('Metadata URLs:');
  const cardOk = await checkUrl('AgentCard (SAID)', urls.agentCard);
  const jsonOk = await checkUrl('agent.json (ERC-8004)', urls.agentJson);
  await checkUrl('Signal mandate', urls.mandate);

  const card = buildConservativeBtcAgentCard({ site, api });
  const agentJson = buildConservativeBtcErc8004AgentJson({ site, api });
  console.log('\n--- SAID (Solana / Phantom) ---');
  console.log('1. Connect Phantom → Account → Security on doxxedcrypto.digital');
  console.log('2. Save same pubkey as Solana treasury → Admin → Platform');
  console.log(`3. npx said-sdk register -k agent-wallet.json -n "${card.name}" --uri "${urls.agentCard}"`);
  console.log('4. Sign tx in Phantom (~0.001 SOL gas)');
  console.log('5. Optional verify badge: npx said-sdk verify (~0.01 SOL)');

  console.log('\n--- The Spawn (Base / ERC-8004) ---');
  console.log(`Metadata URI: ${urls.agentJson}`);
  if (spawnKey) {
    try {
      const res = await fetch('https://thespawn.io/api/v1/agents', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${spawnKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: card.name,
          description: agentJson.description,
          chain_id: 8453,
          metadata_uri: urls.agentJson,
          image_url: agentJson.image,
          x402_support: false,
          services: agentJson.services,
        }),
      });
      const body = await res.json();
      if (res.ok) {
        console.log('Spawn API row created:', body.url ?? body);
        if (body.onchain_registration) {
          console.log('On-chain calldata:', JSON.stringify(body.onchain_registration, null, 2));
        }
      } else {
        console.log('Spawn API error:', body.error ?? body.message ?? res.status);
      }
    } catch (err) {
      console.log('Spawn API request failed:', err instanceof Error ? err.message : err);
    }
  } else {
    console.log('Set THESPAWN_API_KEY or pass --spawn-api-key to call POST /api/v1/agents');
    console.log('Then sign register(string) on Base: 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432');
  }
  console.log('Quality check after mint: npx spawnr@latest check base:<agent_id>');

  console.log('\n--- Admin UI ---');
  console.log(`${site}/admin/agent-registrations`);

  if (!cardOk || !jsonOk) {
    console.log('\nDeploy web (Vercel) before submitting registries.');
    process.exitCode = 1;
  }
}

main();
