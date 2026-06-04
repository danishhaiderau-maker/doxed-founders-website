#!/usr/bin/env node
import fs from 'fs';
import path from 'path';
import { randomBytes } from 'node:crypto';
import { getVaultDir } from './secrets-vault-path.mjs';

const RAILWAY = 'https://doxed-founders-website-production.up.railway.app';
const SITE = 'https://doxxedcrypto.digital';
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

async function testBase(base, pairingCode, prisma) {
  const pairRes = await fetch(`${base}/api/founder-node/pair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: pairingCode, nodeId, label: 'routing-test', platform: 'win32' }),
  });
  const body = await pairRes.json().catch(() => ({}));
  if (!pairRes.ok) {
    console.log(base, 'PAIR', pairRes.status, body);
    return;
  }
  const hbRes = await fetch(`${base}/api/founder-node/heartbeat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `FounderNode ${body.nodeId}:${body.nodeToken}`,
    },
    body: JSON.stringify({
      nodeId: body.nodeId,
      label: 'routing-test',
      platform: 'win32',
      vaultHealthy: true,
    }),
  });
  const bcrypt = await import('bcrypt');
  const row = await prisma.founderNode.findUnique({ where: { nodeId } });
  const ok = await bcrypt.compare(body.nodeToken, row.secretHash);
  console.log(base, { pair: pairRes.status, heartbeat: hbRes.status, bcrypt: ok, hbBody: (await hbRes.text()).slice(0, 80) });
}

loadNeon();
const { PrismaClient } = await import('@prisma/client');
const prisma = new PrismaClient();
const node = await prisma.founderNode.findUnique({ where: { nodeId } });
for (const base of [SITE, RAILWAY]) {
  const pairingCode = code();
  await prisma.founderNodePairingCode.create({
    data: { userId: node.userId, code: pairingCode, expiresAt: new Date(Date.now() + 30 * 60 * 1000) },
  });
  console.log(base, 'code', pairingCode);
  await testBase(base, pairingCode, prisma);
}
await prisma.$disconnect();
