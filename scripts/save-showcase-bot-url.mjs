#!/usr/bin/env node
/** Persist only the source-controlled canonical Fly URL. */
import { PrismaClient } from '@prisma/client';
import { loadVaultEnv } from './load-vault-env.mjs';
import {
  CANONICAL_FLY_BOT_PUBLIC_URL,
  assertCanonicalFlyBotUrl,
} from './home-bot-config.mjs';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
loadVaultEnv(root);

const url = assertCanonicalFlyBotUrl(
  process.argv[2]?.trim() || CANONICAL_FLY_BOT_PUBLIC_URL,
);
const prisma = new PrismaClient();
try {
  await prisma.platformSettings.upsert({
    where: { id: 'default' },
    create: { id: 'default', showcaseBotPublicUrl: url },
    update: { showcaseBotPublicUrl: url },
  });
} finally {
  await prisma.$disconnect();
}
console.log(`showcaseBotPublicUrl locked to ${url}`);
