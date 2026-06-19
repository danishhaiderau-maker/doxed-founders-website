import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

const neonPath = path.join(getVaultDir(), '.env.neon');
if (fs.existsSync(neonPath)) {
  for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    const key = trimmed.slice(0, idx).trim();
    let val = trimmed.slice(idx + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    process.env[key] = val;
  }
}

const query = process.argv[2] ?? 'bitbro4crypto';
const prisma = new PrismaClient();

const users = await prisma.user.findMany({
  where: {
    OR: [
      { platformHandle: { contains: query, mode: 'insensitive' } },
      { twitterHandle: { contains: query, mode: 'insensitive' } },
      { email: { contains: query, mode: 'insensitive' } },
      { name: { contains: query, mode: 'insensitive' } },
    ],
  },
  select: {
    id: true,
    email: true,
    platformHandle: true,
    twitterHandle: true,
    name: true,
    role: true,
  },
});

for (const u of users) {
  const instances = await prisma.tradingAgentInstance.findMany({
    where: { userId: u.id },
    include: { agent: { select: { slug: true, name: true } } },
  });
  const tradeCount = await prisma.signalCycleParticipant.count({ where: { userId: u.id } });
  console.log(JSON.stringify({ user: u, instances, tradeCount }, null, 2));
}

await prisma.$disconnect();
