/**
 * Stage B — publish all pending founder updates (feed, X, community).
 * Uses founder account JWT (repo owner), not admin.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import jwt from 'jsonwebtoken';
import { PrismaClient } from '@prisma/client';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultDir = join(root, '..', 'doxedcryptofounder-secrets', 'vault');

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    map[trimmed.slice(0, idx).trim()] = trimmed
      .slice(idx + 1)
      .trim()
      .replace(/^"|"$/g, '');
  }
  return map;
}

const xSecrets = readDotEnv(
  existsSync(join(root, '.env.x.secrets'))
    ? join(root, '.env.x.secrets')
    : join(vaultDir, '.env.x.secrets'),
);
const neon = readDotEnv(join(vaultDir, '.env.neon'));
const vercel = readDotEnv(join(vaultDir, '.env.vercel.check'));

const apiUrl = (xSecrets.API_URL ?? 'https://doxxedcrypto.digital').replace(/\/$/, '');
const jwtSecret = vercel.JWT_SECRET?.trim();
const dbUrl = neon.DATABASE_URL;

if (!jwtSecret || jwtSecret.length < 32) {
  console.error('Missing JWT_SECRET in vault/.env.vercel.check');
  process.exit(1);
}
if (!dbUrl) {
  console.error('Missing DATABASE_URL in vault/.env.neon');
  process.exit(1);
}

process.env.DATABASE_URL = dbUrl;
const prisma = new PrismaClient();

async function resolveFounderJwt() {
  const repo = process.env.FOUNDER_REPO?.trim() || 'danishhaiderau-maker/doxed-founders-website';
  const founder = await prisma.founder.findFirst({
    where: { githubRepoFullName: repo, userId: { not: null } },
    include: { user: { select: { id: true, email: true, role: true } } },
  });
  if (!founder?.user) {
    const withPending = await prisma.suggestedBuildUpdate.findFirst({
      where: { status: 'PENDING' },
      include: {
        founder: { include: { user: { select: { id: true, email: true, role: true } } } },
      },
    });
    if (!withPending?.founder?.user) {
      throw new Error('No founder user linked to pending updates — log in at founder-den and publish manually.');
    }
    return signJwt(withPending.founder.user);
  }
  return signJwt(founder.user);
}

function signJwt(user) {
  return jwt.sign({ sub: user.id, email: user.email, role: user.role }, jwtSecret, {
    expiresIn: '15m',
  });
}

async function apiFetch(path, token, opts = {}) {
  const res = await fetch(`${apiUrl}/api${path}`, {
    ...opts,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...(opts.headers ?? {}),
    },
  });
  const text = await res.text();
  let json;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = { raw: text };
  }
  return { ok: res.ok, status: res.status, json };
}

console.log('\n=== Stage B: Publish pending founder updates ===\n');
console.log(`API: ${apiUrl}\n`);

const founderJwt = await resolveFounderJwt();
console.log('Founder JWT minted for publish.\n');

const queueBefore = await apiFetch('/copilot/founder-queue', founderJwt);
if (!queueBefore.ok) {
  console.error('Founder queue fetch failed:', queueBefore.status, queueBefore.json);
  await prisma.$disconnect();
  process.exit(1);
}

const items = queueBefore.json?.items ?? [];
const publishItems = items.filter((i) => i.action === 'publish');
console.log(`Queue: ${items.length} item(s), ${publishItems.length} publish action(s)`);
for (const p of publishItems.slice(0, 8)) {
  console.log(`  · ${p.id}: ${p.title}`);
}

const bulk = publishItems.find((i) => i.id === 'publish-all-pending');
const targetId = bulk?.id ?? publishItems[0]?.id;
if (!targetId) {
  console.log('\nNothing to publish — queue is clear.');
  await prisma.$disconnect();
  process.exit(0);
}

console.log(`\nPublishing via queue-action: ${targetId}…\n`);
const result = await apiFetch('/copilot/queue-action', founderJwt, {
  method: 'POST',
  body: JSON.stringify({ itemId: targetId }),
});

console.log('Result:', result.status, JSON.stringify(result.json, null, 2));

const queueAfter = await apiFetch('/copilot/founder-queue', founderJwt);
const afterPublish = (queueAfter.json?.items ?? []).filter((i) => i.action === 'publish');
console.log(`\nAfter: ${afterPublish.length} publish item(s) remaining in queue`);

const pending = await prisma.suggestedBuildUpdate.count({ where: { status: 'PENDING' } });
console.log(`Neon pending suggestions: ${pending}`);

const feed = await fetch(`${apiUrl}/api/feed/unified?limit=3`);
const feedText = await feed.text();
console.log(`\nFeed (${feed.status}):`, feedText.slice(0, 700));

await prisma.$disconnect();
process.exit(result.ok ? 0 : 1);
