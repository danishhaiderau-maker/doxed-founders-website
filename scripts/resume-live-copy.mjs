/**
 * Resume paused Bitfinex live-copy hire instance.
 * Requires explicit --confirm (never run from automation/sync).
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

if (!process.argv.includes('--confirm')) {
  console.error(
    'Refusing to resume live copy without --confirm. User must press Start on Agent Hub.',
  );
  process.exit(1);
}

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

const prisma = new PrismaClient();
const handle = process.argv.slice(2).find((a) => !a.startsWith('-')) ?? 'Cheetah';

async function main() {
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) throw new Error('agent missing');

  const instances = await prisma.tradingAgentInstance.findMany({
    where: {
      agentId: agent.id,
      exchangeProvider: 'bitfinex',
      status: 'PAUSED',
    },
    include: { user: { select: { platformHandle: true, name: true } } },
  });

  const match = instances.find((i) => {
    const label = `${i.user?.platformHandle ?? ''} ${i.user?.name ?? ''}`.toLowerCase();
    return label.includes(handle.toLowerCase());
  });

  if (!match) {
    console.log(`No PAUSED bitfinex instance matching "${handle}".`);
    const all = await prisma.tradingAgentInstance.findMany({
      where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
      include: { user: { select: { platformHandle: true, name: true } } },
    });
    for (const i of all) {
      console.log(`  ${i.user?.platformHandle ?? i.user?.name} → ${i.status}`);
    }
    return;
  }

  await prisma.tradingAgentInstance.update({
    where: { id: match.id },
    data: { status: 'ACTIVE', lastError: null },
  });

  console.log(
    `Resumed live copy: ${match.user?.platformHandle ?? match.user?.name} (${match.id}) → ACTIVE`,
  );
  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
