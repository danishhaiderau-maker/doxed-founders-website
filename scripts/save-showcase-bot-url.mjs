/** Save showcase bot public URL to platformSettings (Admin panel field). */
import { PrismaClient } from '@prisma/client';
import { loadVaultEnv } from './load-vault-env.mjs';
import { fileURLToPath } from 'url';
import path from 'path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
loadVaultEnv(root);

const url =
  process.argv[2]?.trim() || 'https://btc-conservative-agent-production.up.railway.app';

const prisma = new PrismaClient();
await prisma.platformSettings.upsert({
  where: { id: 'default' },
  create: { id: 'default', showcaseBotPublicUrl: url },
  update: { showcaseBotPublicUrl: url },
});
await prisma.$disconnect();
console.log(`showcaseBotPublicUrl = ${url}`);
