import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';

const vault = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'doxedcryptofounder-secrets', 'vault');
const neon = readFileSync(join(vault, '.env.neon'), 'utf8');
const dbUrl = neon.match(/^DATABASE_URL=(.+)$/m)?.[1]?.trim().replace(/^"|"$/g, '');
const xSecrets = readFileSync(join(vault, '.env.x.secrets'), 'utf8');
const bearer = xSecrets.match(/^TWITTER_BEARER_TOKEN=(.+)$/m)?.[1]?.trim().replace(/^"|"$/g, '');

const prisma = new PrismaClient({ datasources: { db: { url: dbUrl } } });
const founders = await prisma.founder.findMany({
  where: { twitterUrl: { not: null } },
  select: { name: true, twitterUrl: true },
});
console.log(`Founders with X URL: ${founders.length}`);
for (const f of founders.slice(0, 8)) {
  const handle = f.twitterUrl?.replace(/.*x\.com\//, '').replace(/^@/, '').split('/')[0];
  console.log(`  ${f.name} -> @${handle}`);
  if (!handle || !bearer) continue;
  const userRes = await fetch(`https://api.twitter.com/2/users/by/username/${handle}`, {
    headers: { Authorization: `Bearer ${bearer}` },
  });
  const userText = await userRes.text();
  console.log(`    user lookup: ${userRes.status} ${userText.slice(0, 120)}`);
}
await prisma.$disconnect();
