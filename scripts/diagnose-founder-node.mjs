#!/usr/bin/env node
/**
 * Diagnose Founder Node pairing for a machine (reads ~/FounderVault, optional Neon).
 * Usage: node scripts/diagnose-founder-node.mjs [nodeId]
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';
import { getVaultDir } from './secrets-vault-path.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const vaultRoot = path.join(os.homedir(), 'FounderVault');
const nodeIdArg = process.argv[2];

function readJson(p) {
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function authHeader(nodeId, nodeToken) {
  return `FounderNode ${nodeId}:${nodeToken}`;
}

async function testApi(cfg) {
  const base = cfg.apiBaseUrl.replace(/\/$/, '');
  const hb = await fetch(`${base}/api/founder-node/heartbeat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: authHeader(cfg.nodeId, cfg.nodeToken),
    },
    body: JSON.stringify({
      nodeId: cfg.nodeId,
      label: cfg.label,
      platform: process.platform,
      appVersion: 'diagnose',
      vaultHealthy: true,
      vaultPath: vaultRoot,
    }),
  });
  const text = await hb.text();
  return { status: hb.status, body: text.slice(0, 300) };
}

async function loadNeon() {
  const neonPath = path.join(getVaultDir(), '.env.neon');
  if (!fs.existsSync(neonPath)) return null;
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
  return process.env.DATABASE_URL?.startsWith('postgres') ? process.env.DATABASE_URL : null;
}

async function queryDb(nodeId) {
  const dbUrl = await loadNeon();
  if (!dbUrl) return null;
  const { PrismaClient } = await import('@prisma/client');
  const prisma = new PrismaClient();
  try {
    const node = await prisma.founderNode.findUnique({ where: { nodeId } });
    const codes = await prisma.founderNodePairingCode.findMany({
      where: node ? { userId: node.userId } : undefined,
      orderBy: { createdAt: 'desc' },
      take: 5,
      select: { code: true, usedAt: true, expiresAt: true, createdAt: true },
    });
    return { node, codes };
  } finally {
    await prisma.$disconnect();
  }
}

async function main() {
  console.log('\n=== Founder Node diagnose ===\n');
  console.log('Vault:', vaultRoot);

  const meta = readJson(path.join(vaultRoot, 'meta.json'));
  const config = readJson(path.join(vaultRoot, 'node-config.json'));
  const backup = readJson(path.join(vaultRoot, 'node-config.json.bak'));
  const nodeId = nodeIdArg || meta?.nodeId || config?.nodeId || backup?.nodeId;

  console.log('meta.nodeId:', meta?.nodeId ?? '(none)');
  console.log('node-config.json:', config ? `paired ${config.pairedAt}` : 'MISSING (not paired locally)');
  console.log('node-config.json.bak:', backup ? `saved ${backup.pairedAt}` : '(none)');

  const cfg = config ?? backup;
  if (cfg?.nodeToken) {
    console.log('\n--- API token test ---');
    const r = await testApi(cfg);
    console.log(`heartbeat: HTTP ${r.status}`);
    console.log(r.body);
    if (r.status === 401) {
      console.log(
        '\nCause: cloud rejected the desktop token (expired or replaced by a newer pairing).\n' +
          'Fix: Founder OS → Settings → Builder → Generate pairing code → paste in Founder Node tray popup (not browser tabs).\n',
      );
    } else if (r.status >= 200 && r.status < 300) {
      console.log('\nToken is valid. If tray shows offline, restart Founder Node from tray only.');
    }
  } else {
    console.log('\nNo local credentials — open Founder Node tray → Pair with Founder OS.');
  }

  if (nodeId) {
    console.log('\n--- Neon (cloud record) ---');
    const db = await queryDb(nodeId);
    if (!db) {
      console.log('Skip DB (no vault/.env.neon)');
    } else if (!db.node) {
      console.log(`Node ${nodeId} not in database — pair again from tray.`);
    } else {
      const online =
        db.node.lastSeenAt &&
        Date.now() - new Date(db.node.lastSeenAt).getTime() < 5 * 60 * 1000;
      console.log({
        label: db.node.label,
        status: db.node.status,
        lastSeenAt: db.node.lastSeenAt?.toISOString(),
        showsOnline: online,
      });
      console.log('Recent pairing codes:', db.codes);
    }
  }

  console.log('');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
