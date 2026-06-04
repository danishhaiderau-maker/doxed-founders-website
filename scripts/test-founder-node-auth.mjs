#!/usr/bin/env node
/** Verify bcrypt round-trip for a founder node row in Neon. */
import fs from 'fs';
import path from 'path';
import bcrypt from 'bcrypt';
import { fileURLToPath } from 'url';
import { getVaultDir } from './secrets-vault-path.mjs';

const nodeId = process.argv[2] || 'node_d130d4e899b842db';
const token = process.argv[3];

const neonPath = path.join(getVaultDir(), '.env.neon');
for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
  const t = line.trim();
  if (!t || t.startsWith('#')) continue;
  const i = t.indexOf('=');
  if (i < 1) continue;
  let v = t.slice(i + 1).trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    v = v.slice(1, -1);
  }
  process.env[t.slice(0, i).trim()] = v;
}

const { PrismaClient } = await import('@prisma/client');
const prisma = new PrismaClient();
const node = await prisma.founderNode.findUnique({ where: { nodeId } });
if (!node) {
  console.log('node not found');
  process.exit(1);
}
console.log('node', node.label, 'lastSeen', node.lastSeenAt);

if (token) {
  const ok = await bcrypt.compare(token, node.secretHash);
  console.log('bcrypt.compare(provided token):', ok);
} else {
  const testToken = `fn_${'a'.repeat(64)}`;
  const hash = await bcrypt.hash(testToken, 10);
  const ok = await bcrypt.compare(testToken, hash);
  console.log('bcrypt round-trip sanity:', ok);
}

await prisma.$disconnect();
