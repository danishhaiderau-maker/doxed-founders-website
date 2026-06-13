#!/usr/bin/env node
/** Mark Spawn (The Spawn / ERC-8004 profile) in Neon after browser registration. */
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PrismaClient } from '@prisma/client';
import { loadVaultEnv } from './load-vault-env.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

const SPAWN_AGENT_ID = process.env.SPAWN_AGENT_ID?.trim() || '55230';
const SPAWN_CHAIN = process.env.SPAWN_CHAIN?.trim() || 'base';
const SPAWN_REGISTRY_URL =
  process.env.SPAWN_REGISTRY_URL?.trim() ||
  `https://thespawn.io/agents/${SPAWN_CHAIN}/${SPAWN_AGENT_ID}`;
const METADATA_URI = 'https://doxxedcrypto.digital/.well-known/agent.json';
const OWNER_EVM = process.env.SPAWN_OWNER_EVM?.trim() || '0x43dc7b908482100595a7fb0b1178360fabbaf16c';

async function main() {
  loadVaultEnv(root);
  if (!process.env.DATABASE_URL?.startsWith('postgres')) {
    console.error('DATABASE_URL missing — set in vault/.env.neon');
    process.exit(1);
  }

  const prisma = new PrismaClient();
  try {
    const agent = await prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
    });
    if (!agent) {
      console.error('conservative-btc agent not found');
      process.exit(1);
    }

    const now = new Date();
    await prisma.agentRegistryEntry.upsert({
      where: { agentId_registry: { agentId: agent.id, registry: 'SPAWN' } },
      create: {
        agentId: agent.id,
        registry: 'SPAWN',
        chainSlug: 'BASE',
        externalId: `${SPAWN_CHAIN}:${SPAWN_AGENT_ID}`,
        ownerAddress: OWNER_EVM,
        metadataUri: METADATA_URI,
        registryUrl: SPAWN_REGISTRY_URL,
        status: 'REGISTERED',
        notes: 'Live on The Spawn — x402 verified, quality B 59/100. spawnr: npx spawnr hire base:55230',
        registeredAt: now,
      },
      update: {
        chainSlug: 'BASE',
        externalId: `${SPAWN_CHAIN}:${SPAWN_AGENT_ID}`,
        ownerAddress: OWNER_EVM,
        metadataUri: METADATA_URI,
        registryUrl: SPAWN_REGISTRY_URL,
        status: 'REGISTERED',
        registeredAt: now,
      },
    });

    // ERC8004_SCAN auto-indexes after Spawn mint — mark pending or registered if profile live
    await prisma.agentRegistryEntry.upsert({
      where: { agentId_registry: { agentId: agent.id, registry: 'ERC8004_SCAN' } },
      create: {
        agentId: agent.id,
        registry: 'ERC8004_SCAN',
        chainSlug: 'BASE',
        externalId: SPAWN_AGENT_ID,
        registryUrl: SPAWN_REGISTRY_URL,
        status: 'REGISTERED',
        notes: 'Indexed via The Spawn profile base:55230',
        registeredAt: now,
      },
      update: {
        registryUrl: SPAWN_REGISTRY_URL,
        externalId: SPAWN_AGENT_ID,
        status: 'REGISTERED',
        registeredAt: now,
      },
    });

    console.log('✓ AgentRegistryEntry SPAWN → REGISTERED');
    console.log('  URL:', SPAWN_REGISTRY_URL);
    console.log('  spawnr:', `npx spawnr hire base:${SPAWN_AGENT_ID}`);
    console.log('  quality check:', `npx spawnr@latest check base:${SPAWN_AGENT_ID}`);
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
