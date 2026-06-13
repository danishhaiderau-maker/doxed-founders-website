#!/usr/bin/env node
/** Mark SAID complete in Neon + set Solana treasury from agent-wallet.json */
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PrismaClient } from '@prisma/client';
import { loadVaultEnv } from './load-vault-env.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const walletPath = join(root, 'agent-wallet.json');

const SAID_WALLET = process.env.SAID_AGENT_WALLET?.trim() || '6dzu562QZieJKHPiZJcqPNkJrMmug5kKSPf7DxrPhPjW';
const SAID_PDA = process.env.SAID_AGENT_PDA?.trim() || '5FaavFtpenHVcRNn5saxdQ8BhurFn22DwhBmv7KHVXYA';
const SAID_REGISTER_TX =
  process.env.SAID_REGISTER_TX?.trim() ||
  '5iYBYtHSqhX5YPgVhMo8jhLMU3MeC2NERL45vgk4WeQDSxhmsqtZE7KVtwzA2wyqb6FxKD5xbDG5uSEr54XGsTvR';
const SAID_PROFILE = `https://www.saidprotocol.com/agents/${SAID_WALLET}`;
const METADATA_URI = 'https://doxxedcrypto.digital/.well-known/agent-card.json';

async function main() {
  loadVaultEnv(root);
  if (!process.env.DATABASE_URL?.startsWith('postgres')) {
    console.error('DATABASE_URL missing — set in vault/.env.neon');
    process.exit(1);
  }
  if (!existsSync(walletPath)) {
    console.warn('agent-wallet.json not found — using SAID_WALLET env/default');
  }

  const prisma = new PrismaClient();
  try {
    await prisma.platformTreasury.upsert({
      where: { id: 'default' },
      create: { id: 'default', solanaTreasuryAddress: SAID_WALLET },
      update: { solanaTreasuryAddress: SAID_WALLET },
    });
    console.log('✓ Platform Solana treasury:', SAID_WALLET);

    const agent = await prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
    });
    if (!agent) {
      console.warn('conservative-btc agent not found — skip registry entry');
      return;
    }

    const now = new Date();
    await prisma.agentRegistryEntry.upsert({
      where: { agentId_registry: { agentId: agent.id, registry: 'SAID' } },
      create: {
        agentId: agent.id,
        registry: 'SAID',
        chainSlug: 'SOLANA',
        externalId: SAID_PDA,
        ownerAddress: SAID_WALLET,
        metadataUri: METADATA_URI,
        registryUrl: SAID_PROFILE,
        txSignature: SAID_REGISTER_TX,
        status: 'VERIFIED',
        notes: 'Registered + verified via npm run register:said-simple',
        registeredAt: now,
        verifiedAt: now,
      },
      update: {
        chainSlug: 'SOLANA',
        externalId: SAID_PDA,
        ownerAddress: SAID_WALLET,
        metadataUri: METADATA_URI,
        registryUrl: SAID_PROFILE,
        txSignature: SAID_REGISTER_TX,
        status: 'VERIFIED',
        verifiedAt: now,
        registeredAt: now,
      },
    });
    console.log('✓ AgentRegistryEntry SAID → VERIFIED');
    console.log('  Profile:', SAID_PROFILE);
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
