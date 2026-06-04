#!/usr/bin/env node
/**
 * Repair local Founder Node credentials when cloud token was rotated
 * (e.g. extra pairing from browser/tests) but ~/FounderVault/node-config.json is stale.
 *
 * Usage:
 *   node scripts/repair-founder-node.mjs              # auto-issue code + pair
 *   node scripts/repair-founder-node.mjs ABCD1234    # use existing unused code
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'url';
import { getVaultDir } from './secrets-vault-path.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const API_BASE = (process.env.FOUNDER_OS_API_URL ?? 'https://doxxedcrypto.digital').replace(
  /\/$/,
  '',
);
const vaultRoot = path.join(os.homedir(), 'FounderVault');

function loadNeonEnv() {
  const neonPath = path.join(getVaultDir(), '.env.neon');
  if (!fs.existsSync(neonPath)) throw new Error('Missing vault/.env.neon');
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
}

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

function writeJson(p, data) {
  fs.writeFileSync(p, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
}

function generatePairingCode() {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  for (let i = 0; i < 8; i += 1) {
    code += alphabet[randomBytes(1)[0] % alphabet.length];
  }
  return code;
}

function authHeader(nodeId, nodeToken) {
  return `FounderNode ${nodeId}:${nodeToken}`;
}

async function pair(code, nodeId, label) {
  const res = await fetch(`${API_BASE}/api/founder-node/pair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code,
      nodeId,
      label,
      platform: process.platform,
      appVersion: 'repair-script',
    }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.message ?? `Pair failed HTTP ${res.status}`);
  }
  return body;
}

async function heartbeat(nodeId, nodeToken, label) {
  const res = await fetch(`${API_BASE}/api/founder-node/heartbeat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: authHeader(nodeId, nodeToken),
    },
    body: JSON.stringify({
      nodeId,
      label,
      platform: process.platform,
      appVersion: 'repair-script',
      vaultHealthy: true,
      vaultPath: vaultRoot,
    }),
  });
  const text = await res.text();
  return { status: res.status, body: text };
}

async function main() {
  loadNeonEnv();
  const { PrismaClient } = await import('@prisma/client');
  const prisma = new PrismaClient();

  const metaPath = path.join(vaultRoot, 'meta.json');
  if (!fs.existsSync(metaPath)) throw new Error(`Missing ${metaPath}`);
  const meta = readJson(metaPath);
  const nodeId = meta.nodeId;
  if (!nodeId) throw new Error('meta.json missing nodeId');

  const existing = readJson(path.join(vaultRoot, 'node-config.json'));
  const label = existing?.label ?? `${os.hostname()} Founder Node`;

  let code = process.argv[2]?.trim().toUpperCase();
  if (!code) {
    const node = await prisma.founderNode.findUnique({ where: { nodeId } });
    if (!node) throw new Error(`Node ${nodeId} not in database`);
    code = generatePairingCode();
    const expiresAt = new Date(Date.now() + 30 * 60 * 1000);
    await prisma.founderNodePairingCode.create({
      data: { userId: node.userId, code, expiresAt },
    });
    console.log('Issued repair pairing code:', code, '(30 min)');
  }

  console.log('Pairing', nodeId, '→', API_BASE);
  const result = await pair(code, nodeId, label);
  const hb = await heartbeat(result.nodeId, result.nodeToken, label);
  console.log('heartbeat:', hb.status, hb.body.slice(0, 120));

  if (hb.status !== 200 && hb.status !== 201) {
    throw new Error('Heartbeat failed after pair — aborting config write');
  }

  const bcrypt = await import('bcrypt');
  const row = await prisma.founderNode.findUnique({ where: { nodeId } });
  const ok = await bcrypt.compare(result.nodeToken, row.secretHash);
  console.log('bcrypt.compare after pair:', ok);
  if (!ok) throw new Error('DB hash mismatch after pair (server bug)');

  const prevPath = path.join(vaultRoot, 'node-config.json');
  if (fs.existsSync(prevPath)) {
    fs.copyFileSync(prevPath, `${prevPath}.bak`);
  }

  const config = {
    version: 1,
    apiBaseUrl: API_BASE,
    nodeId: result.nodeId,
    nodeToken: result.nodeToken,
    label,
    pairedAt: new Date().toISOString(),
    ollama: existing?.ollama ?? {
      enabled: false,
      baseUrl: 'http://127.0.0.1:11434',
      model: 'llama3.2',
    },
  };
  writeJson(prevPath, config);
  console.log('Wrote', prevPath);
  console.log('Restart Founder Node tray app (or tray → Sync now) if it was running.');

  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
