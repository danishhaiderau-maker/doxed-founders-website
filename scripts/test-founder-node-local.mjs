#!/usr/bin/env node
import fs from 'fs';
import path from 'path';
import { randomBytes } from 'node:crypto';
import { getVaultDir } from './secrets-vault-path.mjs';

const base = process.argv[2] ?? 'http://127.0.0.1:4010';
const nodeId = 'node_d130d4e899b842db';

function loadNeon() {
  for (const line of fs.readFileSync(path.join(getVaultDir(), '.env.neon'), 'utf8').split('\n')) {
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
}

function code() {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let c = '';
  for (let i = 0; i < 8; i += 1) c += alphabet[randomBytes(1)[0] % alphabet.length];
  return c;
}

loadNeon();
const { PrismaClient } = await import('@prisma/client');
const prisma = new PrismaClient();
const node = await prisma.founderNode.findUnique({ where: { nodeId } });
const pairingCode = code();
await prisma.founderNodePairingCode.create({
  data: { userId: node.userId, code: pairingCode, expiresAt: new Date(Date.now() + 30 * 60 * 1000) },
});

const pairRes = await fetch(`${base}/api/founder-node/pair`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ code: pairingCode, nodeId, label: 'local-test', platform: 'win32' }),
});
const body = await pairRes.json();
console.log('pair', pairRes.status, body.nodeId ? 'ok' : body);

const hbRes = await fetch(`${base}/api/founder-node/heartbeat`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `FounderNode ${body.nodeId}:${body.nodeToken}`,
  },
  body: JSON.stringify({
    nodeId: body.nodeId,
    label: 'local-test',
    platform: 'win32',
    vaultHealthy: true,
  }),
});
console.log('heartbeat', hbRes.status, await hbRes.text());
await prisma.$disconnect();
