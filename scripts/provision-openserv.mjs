#!/usr/bin/env node
/**
 * Provision Conservative BTC Agent on OpenServ (agent + workflow) via API.
 * Requires OPENSERV_USER_API_KEY in vault (Developer → API keys on platform.openserv.ai).
 * Agent secret keys (per-agent) are stored separately as OPENSERV_AGENT_API_KEY.
 *
 * Usage:
 *   npm run provision:openserv
 */
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadVaultEnv } from './load-vault-env.mjs';
import { CONSERVATIVE_BTC_DIRECTORY_PROFILE as P } from '../packages/utils/dist/agent-directory-submissions.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
loadVaultEnv(root);

const USER_KEY =
  process.env.OPENSERV_USER_API_KEY?.trim() ||
  process.env.OPENSERV_PLATFORM_API_KEY?.trim() ||
  process.env.OPENSERV_API_KEY?.trim() ||
  process.env.OPENSERV_AGENT_API_KEY?.trim() ||
  '';

const AGENT_NAME = P.name;
const CAPABILITIES = `Live BTC perpetual signal cycles via REST Signal Cycle API. Exchange-neutral ENSE intents for Hyperliquid, Bitfinex, Bybit, and other perp venues. Public hub: ${P.hubUrl}. Docs: ${P.docsUrl}. Mandate API: ${P.mandateUrl}. Success fee 10% on profit only. Not financial advice.`;

const WORKFLOW_GOAL = `Conservative BTC Agent helps users discover and subscribe to exchange-neutral BTC perpetual signal cycles on Doxxed Crypto Founder. The workflow explains how to observe the live showcase at ${P.hubUrl}, read subscriber documentation at ${P.docsUrl}, obtain a Signal API key, poll for ENSE intents, execute on the user's own exchange with mandatory stop-loss at fill, report lifecycle events, and understand success-fee billing (10% of profit on winning closes only, zero on losses). The agent never executes trades on behalf of users and always includes the informational disclaimer.`;

async function main() {
  if (!USER_KEY) {
    console.error(`
Missing OpenServ platform API key.

ONE-TIME setup (30 seconds):
1. Open https://platform.openserv.ai → Developer → Profile / API (or account settings)
2. Copy your USER / Platform API key (not the per-agent secret unless that's all you have)
3. Add to vault file (never commit):
   ${join(dirname(root), 'doxedcryptofounder-secrets', 'vault', '.env')}

   OPENSERV_USER_API_KEY=your_key_here

4. Re-run: npm run provision:openserv

If you only have the per-agent secret from "Create Secret Key", paste it as:
   OPENSERV_AGENT_API_KEY=sk-...
We will try that as fallback.
`);
    process.exit(1);
  }

  const { PlatformClient, triggers } = await import('@openserv-labs/client');
  const client = new PlatformClient({ apiKey: USER_KEY });

  console.log('\n=== OpenServ provision — Conservative BTC Agent ===\n');

  // Idempotent: find existing agent by name
  let agent;
  const owned = await client.agents.searchOwned({ query: 'Conservative BTC' }).catch(() => ({ items: [] }));
  agent = owned.items?.find((a) => a.name?.includes('Conservative')) ?? owned.items?.[0];

  if (agent?.id) {
    console.log(`Updating existing agent id=${agent.id}`);
    agent = await client.agents.update({
      id: agent.id,
      name: AGENT_NAME,
      capabilities_description: CAPABILITIES,
      endpoint_url: P.hubUrl,
    });
  } else {
    console.log('Creating agent…');
    agent = await client.agents.create({
      name: AGENT_NAME,
      capabilities_description: CAPABILITIES,
      endpoint_url: P.hubUrl,
    });
    console.log(`Created agent id=${agent.id}`);
  }

  const agentId = agent.id ?? agent.agent?.id;
  if (!agentId) throw new Error('No agent id returned from OpenServ');

  const workflows = await client.workflows.list().catch(() => ({ items: [] }));
  let workflow = workflows.items?.find((w) => w.name?.includes('Conservative BTC'));

  if (workflow?.id) {
    console.log(`Updating workflow id=${workflow.id}`);
    workflow = await client.workflows.sync({
      id: workflow.id,
      triggers: [triggers.manual()],
      tasks: [
        {
          name: 'explain-signals',
          agentId,
          description:
            'Explain Conservative BTC Agent signal subscription, fees, docs links, and exchange-neutral execution rules.',
        },
      ],
    });
    await client.workflows.setRunning({ id: workflow.id });
  } else {
    console.log('Creating workflow…');
    workflow = await client.workflows.create({
      name: 'Conservative BTC Signal Guide',
      goal: WORKFLOW_GOAL,
      triggers: [triggers.manual()],
      tasks: [
        {
          name: 'explain-signals',
          agentId,
          description:
            'Explain Conservative BTC Agent signal subscription, fees, docs links, and exchange-neutral execution rules.',
        },
      ],
    });
    await client.workflows.setRunning({ id: workflow.id });
    console.log(`Created workflow id=${workflow.id}`);
  }

  console.log('\n=== DONE ===');
  console.log('Agent ID:   ', agentId);
  console.log('Workflow ID:', workflow.id ?? workflow.workflow?.id);
  console.log('Hub:        ', P.hubUrl);
  console.log('\nNext: Open https://platform.openserv.ai → Your Agents → Submit for Review');
  console.log('Then: Admin → Agent registrations → Mark SKILLS_SH\n');
}

main().catch((err) => {
  console.error('\nOpenServ provision failed:', err.message || err);
  if (String(err.message || err).includes('401') || String(err.message || err).includes('403')) {
    console.error('→ Check OPENSERV_USER_API_KEY is your platform user key, not an expired agent secret.');
  }
  process.exit(1);
});
