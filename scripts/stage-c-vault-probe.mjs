/**
 * Stage C server probe — vault relay privacy (no device required).
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    map[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^"|"$/g, '');
  }
  return map;
}

const dbUrl = readDotEnv(join(vault, '.env.neon')).DATABASE_URL;
if (!dbUrl) {
  console.error('Missing DATABASE_URL');
  process.exit(1);
}

process.env.DATABASE_URL = dbUrl;
const prisma = new PrismaClient();

const SUSPICIOUS_PLAINTEXT = [
  /Complete Security Hardening/i,
  /OWASP ZAP/i,
  /private notes?:/i,
  /## Roadmap/i,
  /current_goal/i,
];

function looksLikePlaintextVaultLeak(text) {
  if (!text || text.length < 20) return false;
  return SUSPICIOUS_PLAINTEXT.some((re) => re.test(text));
}

console.log('\n=== Stage C vault probe (server) ===\n');

const relays = await prisma.founderNodeVaultRelay.findMany({
  select: {
    nodeId: true,
    label: true,
    encryptedVaultBlob: true,
    updatedAt: true,
    user: { select: { email: true } },
  },
  take: 20,
});

console.log(`Vault relays: ${relays.length}`);
for (const r of relays) {
  const blob = r.encryptedVaultBlob ?? '';
  const leak = looksLikePlaintextVaultLeak(blob);
  console.log(
    `  ${r.label ?? r.nodeId} (${r.user?.email ?? 'user'}) blob=${blob.length} chars leak=${leak ? 'FAIL' : 'ok'} updated=${r.updatedAt.toISOString()}`,
  );
  if (leak) {
    console.error('  ERROR: relay blob looks like plaintext');
    process.exit(1);
  }
}

const deviceSyncs = await prisma.projectMemoryDeviceSync.findMany({
  select: { userId: true, deviceLabel: true, payload: true, updatedAt: true },
  take: 20,
});

let syncLeaks = 0;
for (const row of deviceSyncs) {
  const raw = JSON.stringify(row.payload ?? {});
  if (looksLikePlaintextVaultLeak(raw)) {
    syncLeaks += 1;
    console.log(`  WARN deviceSync ${row.deviceLabel}: possible plaintext in metadata`);
  }
}

const nodes = await prisma.founderNode.findMany({
  where: { status: 'online' },
  select: { label: true, platform: true, lastSeenAt: true },
  take: 5,
});
console.log(`\nOnline Founder Nodes: ${nodes.length}`);
for (const n of nodes) {
  console.log(`  ${n.label} (${n.platform}) lastSeen=${n.lastSeenAt?.toISOString() ?? '—'}`);
}

console.log('\nSummary:');
console.log(`  Relays with encrypted blob: ${relays.filter((r) => (r.encryptedVaultBlob?.length ?? 0) > 0).length}`);
console.log(`  Device sync plaintext warnings: ${syncLeaks}`);
console.log(
  syncLeaks === 0 && relays.some((r) => (r.encryptedVaultBlob?.length ?? 0) > 100)
    ? '\n✓ Server probe PASS — complete device steps in docs/STAGE_C_ANDROID_VAULT.md'
    : '\n△ Server probe PARTIAL — pair Founder Node / pull on Android to finish Stage C',
);

await prisma.$disconnect();
process.exit(syncLeaks > 0 ? 1 : 0);
