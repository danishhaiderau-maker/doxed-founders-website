#!/usr/bin/env node
// scripts/verify-stack-deployed.mjs
//
// Post-deploy verification harness. Verifies that code actually reached
// production across four surfaces (GitHub, Railway, Vercel, Neon) and prints
// a single GREEN/RED verdict table.
//
// Exit 0 = all green (yellow allowed), 1 = any red.
//
// Usage:
//   node scripts/verify-stack-deployed.mjs                  # checks latest state
//   node scripts/verify-stack-deployed.mjs --sha <gitsha>   # checks a specific commit landed + deployed
//   node scripts/verify-stack-deployed.mjs --json           # machine-readable output
//
// Why this exists: the AI agent was declaring tasks "done" without verifying
// production. Railway was broken for 2+ days and nobody noticed. This script
// is the gate that catches that.

import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';
import { connect as tlsConnect } from 'node:tls';
import { connect as netConnect } from 'node:net';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(dirname(root), 'doxedcryptofounder-secrets', 'vault');

// ─── Constants ────────────────────────────────────────────────────────────
const RAILWAY_GQL = 'https://backboard.railway.com/graphql/v2';
const RAILWAY_PROJECT_ID = 'b63a0613-ba29-441d-93a2-cca1a2c1a902';
const RAILWAY_HEALTH_URL =
  'https://doxed-founders-website-production.up.railway.app/api/health/live';

const VERCEL_API = 'https://api.vercel.com';
const VERCEL_PROJECT_NAME = 'doxed-founders-website';
const VERCEL_TEAM_ID = 'team_KZnVXkwWtzHaT0EfltPcy3LE';
const VERCEL_SITE_URL = 'https://doxxedcrypto.digital';

const HTTP_TIMEOUT_MS = 10000;
const NEON_TIMEOUT_MS = 8000;

// ─── Args ─────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const jsonMode = args.includes('--json');
const shaIdx = args.indexOf('--sha');
const targetSha =
  shaIdx >= 0 && args[shaIdx + 1] ? args[shaIdx + 1].replace(/^sha=?/i, '') : null;

// ─── Vault reader (reused pattern from railway-api-status.mjs) ────────────
function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    let v = t.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    if (v) map[t.slice(0, i).trim()] = v;
  }
  return map;
}

// Token resolution: env first, then vault files in priority order.
function resolveRailwayToken() {
  if (process.env.RAILWAY_TOKEN?.trim()) return process.env.RAILWAY_TOKEN.trim();
  // .env.x.secrets is the documented primary, but the live token currently
  // lives in .env.vercel.check; .env.production is the spec fallback.
  for (const name of ['.env.x.secrets', '.env.vercel.check', '.env.production']) {
    const v = readDotEnv(join(vault, name)).RAILWAY_TOKEN?.trim();
    if (v) return v;
  }
  return null;
}

function resolveVercelToken() {
  if (process.env.VERCEL_TOKEN?.trim()) return process.env.VERCEL_TOKEN.trim();
  if (process.env.VERCEL_API_TOKEN?.trim()) return process.env.VERCEL_API_TOKEN.trim();
  for (const name of ['.env.vercel.token', '.env.x.secrets', '.env.vercel.check', '.env.production']) {
    const m = readDotEnv(join(vault, name));
    const v = (m.VERCEL_TOKEN ?? m.VERCEL_API_TOKEN)?.trim();
    if (v) return v;
  }
  return null;
}

function resolveDatabaseUrl() {
  if (process.env.DATABASE_URL?.trim()) return process.env.DATABASE_URL.trim();
  for (const name of ['.env.neon', '.env.vercel.check', '.env.production']) {
    const v = readDotEnv(join(vault, name)).DATABASE_URL?.trim();
    if (v) return v;
  }
  return null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────
async function fetchWithTimeout(url, opts = {}, timeoutMs = HTTP_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

function shortSha(sha) {
  return sha ? sha.slice(0, 8) : '?';
}

function isAncestorOrEqual(ancestor, descendant) {
  // Returns true if `ancestor` is an ancestor of (or equal to) `descendant`.
  if (!ancestor || !descendant) return false;
  if (ancestor === descendant) return true;
  if (ancestor.startsWith(descendant) || descendant.startsWith(ancestor)) return true;
  try {
    execSync(`git merge-base --is-ancestor ${ancestor} ${descendant}`, {
      stdio: 'ignore',
      cwd: root,
    });
    return true;
  } catch {
    return false;
  }
}

function fmtTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toISOString().slice(5, 16).replace('T', ' ');
  } catch {
    return iso.slice(0, 16);
  }
}

// ─── Result type ──────────────────────────────────────────────────────────
// status: 'GREEN' | 'RED' | 'YELLOW'
const GREEN = 'GREEN';
const RED = 'RED';
const YELLOW = 'YELLOW';

// ─── Check 1: GitHub ──────────────────────────────────────────────────────
function checkGitHub() {
  const r = { surface: 'GITHUB', status: GREEN, detail: '' };
  let originMaster;
  let localMaster;
  try {
    originMaster = execSync('git rev-parse origin/master', { encoding: 'utf8', cwd: root }).trim();
  } catch {
    r.status = YELLOW;
    r.detail = 'origin/master not available (git fetch needed?)';
    return r;
  }
  try {
    localMaster = execSync('git rev-parse master', { encoding: 'utf8', cwd: root }).trim();
  } catch {
    localMaster = null;
  }

  if (targetSha) {
    // Confirm the target SHA is present on origin/master.
    if (isAncestorOrEqual(targetSha, originMaster)) {
      r.detail = `origin/master contains ${shortSha(targetSha)} (tip ${shortSha(originMaster)})`;
    } else {
      r.status = RED;
      r.detail = `${shortSha(targetSha)} NOT on origin/master (tip ${shortSha(originMaster)})`;
    }
    return r;
  }

  if (localMaster && localMaster !== originMaster) {
    // Check if local is ahead (unpushed).
    try {
      execSync(`git merge-base --is-ancestor ${originMaster} ${localMaster}`, {
        stdio: 'ignore',
        cwd: root,
      });
      // origin is ancestor of local => local ahead => unpushed commits
      r.status = RED;
      r.detail = `local master ahead of origin (unpushed): ${shortSha(localMaster)} > ${shortSha(originMaster)}`;
    } catch {
      r.status = YELLOW;
      r.detail = `local/origin diverged: ${shortSha(localMaster)} vs ${shortSha(originMaster)}`;
    }
    return r;
  }

  r.detail = `origin/master at ${shortSha(originMaster)}`;
  return r;
}

// ─── Check 2: Railway ─────────────────────────────────────────────────────
async function checkRailway(token, target) {
  const r = { surface: 'RAILWAY', status: GREEN, detail: '', meta: {} };
  const notes = [];
  let latestDeploy = null;

  if (token) {
    try {
      // Try the project-scoped query first (per spec).
      const q = `{ project(id: "${RAILWAY_PROJECT_ID}") { id deployments(first: 8) { edges { node { id status createdAt meta } } } } }`;
      const res = await fetchWithTimeout(
        RAILWAY_GQL,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: q }),
        },
        HTTP_TIMEOUT_MS,
      );
      const j = await res.json();
      if (j?.errors?.length && !j?.data?.project) {
        // project(id:) not authorized with this token scope — fall back to health-only.
        notes.push('deploy API: token scope denies project read');
      } else {
        const deps =
          j?.data?.project?.deployments?.edges?.map((e) => e.node) ??
          [];
        // Find latest non-skipped (SKIPPED == redundant redeploy, not a real build).
        latestDeploy = deps.find((d) => d.status !== 'SKIPPED') ?? deps[0] ?? null;
        if (latestDeploy) {
          const meta =
            typeof latestDeploy.meta === 'string'
              ? safeJson(latestDeploy.meta)
              : latestDeploy.meta;
          latestDeploy.meta = meta;
        }
      }
    } catch (e) {
      notes.push(`deploy API error: ${e.message}`);
    }
  } else {
    notes.push('no RAILWAY_TOKEN');
  }

  // Evaluate deploy status if we got one.
  if (latestDeploy) {
    const st = latestDeploy.status;
    const sha = latestDeploy.meta?.commitSha;
    if (st === 'SUCCESS') {
      if (target && sha) {
        if (isAncestorOrEqual(target, sha)) {
          notes.push(`latest SUCCESS deploys ${shortSha(sha)} (${fmtTime(latestDeploy.createdAt)})`);
        } else {
          r.status = RED;
          notes.push(
            `${shortSha(target)} not yet deployed (latest SUCCESS is ${shortSha(sha)} at ${fmtTime(latestDeploy.createdAt)})`,
          );
        }
      }
    } else if (st === 'FAILED' || st === 'CRASHED') {
      r.status = RED;
      notes.push(`latest deploy ${st} (${shortSha(sha)}, ${fmtTime(latestDeploy.createdAt)})`);
    } else if (st === 'BUILDING' || st === 'QUEUED' || st === 'UPLOADING') {
      if (r.status === GREEN) r.status = YELLOW;
      notes.push(`deploy ${st} (${shortSha(sha)}, ${fmtTime(latestDeploy.createdAt)})`);
    } else {
      notes.push(`deploy ${st} (${shortSha(sha)})`);
    }
  } else if (token) {
    // Token present but no deployment record returned — degrade to health-only.
    if (r.status === GREEN) r.status = YELLOW;
  }

  // Health endpoint is the hard gate — even with no token, if this is down the
  // surface is RED. This is what catches "Railway broken for 2 days".
  try {
    const res = await fetchWithTimeout(RAILWAY_HEALTH_URL, { method: 'GET' }, HTTP_TIMEOUT_MS);
    if (res.ok) {
      notes.push('health 200');
    } else {
      r.status = RED;
      notes.push(`health HTTP ${res.status}`);
    }
  } catch (e) {
    r.status = RED;
    notes.push(`health unreachable: ${e.message}`);
  }

  r.detail = notes.join(' · ');
  r.meta = {
    latestStatus: latestDeploy?.status ?? null,
    latestSha: latestDeploy?.meta?.commitSha ?? null,
    latestCreatedAt: latestDeploy?.createdAt ?? null,
  };
  return r;
}

// ─── Check 3: Vercel ──────────────────────────────────────────────────────
async function checkVercel(token, target) {
  const r = { surface: 'VERCEL', status: GREEN, detail: '', meta: {} };

  if (!token) {
    r.status = YELLOW;
    r.detail = 'no token — skipping';
    return r;
  }

  const notes = [];
  let latestProd = null;

  try {
    const url =
      `${VERCEL_API}/v6/deployments?app=${VERCEL_PROJECT_NAME}` +
      `&target=production&limit=5&teamId=${VERCEL_TEAM_ID}`;
    const res = await fetchWithTimeout(
      url,
      { headers: { Authorization: `Bearer ${token}` } },
      HTTP_TIMEOUT_MS,
    );
    if (!res.ok) {
      r.status = RED;
      r.detail = `API HTTP ${res.status}`;
      return r;
    }
    const j = await res.json();
    const deps = (j.deployments ?? []).filter(
      (d) => d.target === 'production' && d.readyState !== 'CANCELED' && d.readyState !== 'SKIPPED'
    );
    latestProd = deps[0] ?? null;
  } catch (e) {
    r.status = YELLOW;
    r.detail = `API error: ${e.message}`;
    return r;
  }

  if (latestProd) {
    const st = latestProd.readyState;
    const sha = latestProd.meta?.githubCommitSha ?? null;
    if (st === 'READY') {
      if (target && sha) {
        if (isAncestorOrEqual(target, sha)) {
          notes.push(`READY ${shortSha(sha)}`);
        } else {
          r.status = RED;
          notes.push(`${shortSha(target)} not deployed (READY is ${shortSha(sha)})`);
        }
      } else {
        notes.push(`READY ${shortSha(sha)}`);
      }
    } else if (st === 'ERROR' || st === 'FAILED' || st === 'CANCELED') {
      r.status = RED;
      notes.push(`${st} ${shortSha(sha)}`);
    } else if (st === 'BUILDING' || st === 'INITIALIZING' || st === 'QUEUED') {
      if (r.status === GREEN) r.status = YELLOW;
      notes.push(`${st} ${shortSha(sha)}`);
    } else {
      notes.push(`${st} ${shortSha(sha)}`);
    }
  } else {
    r.status = YELLOW;
    notes.push('no production deployment found');
  }

  // HTTP-check the site.
  try {
    const res = await fetchWithTimeout(VERCEL_SITE_URL, { method: 'GET', redirect: 'follow' }, HTTP_TIMEOUT_MS);
    if (res.ok) {
      notes.push(`${VERCEL_SITE_URL} 200`);
    } else {
      r.status = RED;
      notes.push(`${VERCEL_SITE_URL} HTTP ${res.status}`);
    }
  } catch (e) {
    r.status = RED;
    notes.push(`site unreachable: ${e.message}`);
  }

  r.detail = notes.join(' · ');
  r.meta = {
    latestState: latestProd?.readyState ?? null,
    latestSha: latestProd?.meta?.githubCommitSha ?? null,
  };
  return r;
}

// ─── Check 4: Neon ────────────────────────────────────────────────────────
//
// We avoid spawning `npx prisma db execute` here because the spawnSync of npx
// destabilises the Node process on Windows (hard crash on exit, 0xC0000409).
// pg isn't installed either. Instead we do a TLS handshake to the Neon
// pooler host — sufficient as a "is the DB reachable" liveness gate, and
// fast/pure-Node. A real query check belongs in the API's own health endpoint
// (already covered by the Railway row).
function parsePgHost(connStr) {
  // postgresql://user:pass@host:port/db?...
  const m = String(connStr).match(/^postgres(?:ql)?:\/\/[^@]*@([^:/?#]+)/);
  return m ? m[1] : null;
}

async function checkNeon(databaseUrl) {
  const r = { surface: 'NEON', status: GREEN, detail: '', meta: {} };
  if (!databaseUrl) {
    r.status = YELLOW;
    r.detail = 'no DATABASE_URL — skipping';
    return r;
  }

  const host = parsePgHost(databaseUrl);
  if (!host) {
    r.status = YELLOW;
    r.detail = 'DATABASE_URL is not a parseable postgres URL — skipping';
    return r;
  }

  const start = Date.now();
  await new Promise((resolve) => {
    let settled = false;
    const done = (ok, msg) => {
      if (settled) return;
      settled = true;
      const elapsed = Date.now() - start;
      r.meta.latencyMs = elapsed;
      if (ok) {
        r.detail = `reachable ${host} TLS handshake in ${elapsed}ms`;
      } else {
        r.status = RED;
        r.detail = `${msg} (${host}, ${elapsed}ms)`;
      }
      resolve();
    };

    // Neon requires TLS. Open a TCP socket and immediately upgrade to TLS.
    const sock = netConnect({ host, port: 5432, timeout: NEON_TIMEOUT_MS }, () => {
      const tls = tlsConnect(
        { socket: sock, servername: host, rejectUnauthorized: true },
        () => done(true),
      );
      tls.setTimeout(NEON_TIMEOUT_MS);
      tls.on('error', (e) => done(false, `TLS error: ${e.code ?? e.message}`));
      tls.on('timeout', () => done(false, 'TLS handshake timed out'));
    });
    sock.setTimeout(NEON_TIMEOUT_MS);
    sock.on('error', (e) => done(false, `connect error: ${e.code ?? e.message}`));
    sock.on('timeout', () => done(false, 'TCP connect timed out'));
  });
  return r;
}

// ─── Utilities ────────────────────────────────────────────────────────────
function safeJson(s) {
  try {
    return JSON.parse(s);
  } catch {
    return {};
  }
}

// ─── Output ───────────────────────────────────────────────────────────────
function symbol(status) {
  if (status === GREEN) return '[OK]   GREEN';
  if (status === RED) return '[FAIL] RED  ';
  return '[WARN] YELLOW';
}

function renderHuman(results, sha) {
  const line = '═'.repeat(57);
  const shaLabel = sha ? `  (sha: ${shortSha(sha)})` : '  (latest)';
  const out = [];
  out.push(line);
  out.push(`  STACK DEPLOY VERIFICATION${shaLabel}`);
  out.push(line);
  for (const r of results) {
    out.push(`  ${r.surface.padEnd(9)} ${symbol(r.status)}  ${r.detail}`);
  }
  out.push(line);
  const reds = results.filter((r) => r.status === RED);
  if (reds.length === 0) {
    const yellows = results.filter((r) => r.status === YELLOW);
    if (yellows.length) {
      out.push('  RESULT: [WARN] PASS with warnings — ' + yellows.map((y) => y.surface).join(', ') + ' yellow.');
    } else {
      out.push('  RESULT: [OK] PASS — all surfaces green.');
    }
  } else {
    const reason =
      reds[0].surface === 'RAILWAY'
        ? 'Railway deploy broken.'
        : reds[0].surface === 'VERCEL'
          ? 'Vercel deploy broken.'
          : reds[0].surface === 'NEON'
            ? 'Neon unreachable.'
            : 'GitHub not in sync.';
    out.push(`  RESULT: [FAIL] FAIL — ${reds[0].surface} RED. ${reason} Fix before declaring done.`);
  }
  out.push(line);
  return out.join('\n');
}

// ─── Main ─────────────────────────────────────────────────────────────────
async function main() {
  const railwayToken = resolveRailwayToken();
  const vercelToken = resolveVercelToken();
  const databaseUrl = resolveDatabaseUrl();

  const [github, railway, vercel, neon] = await Promise.all([
    Promise.resolve(checkGitHub()),
    checkRailway(railwayToken, targetSha),
    checkVercel(vercelToken, targetSha),
    checkNeon(databaseUrl),
  ]);

  const results = [github, railway, vercel, neon];

  if (jsonMode) {
    const reds = results.filter((r) => r.status === RED).length;
    const payload = {
      timestamp: new Date().toISOString(),
      targetSha: targetSha ?? null,
      overall: reds > 0 ? 'FAIL' : 'PASS',
      surfaces: results,
    };
    process.stdout.write(JSON.stringify(payload, null, 2) + '\n');
  } else {
    process.stdout.write(renderHuman(results, targetSha) + '\n');
  }

  process.exit(results.some((r) => r.status === RED) ? 1 : 0);
}

main().catch((err) => {
  console.error('verify-stack-deployed: fatal:', err);
  process.exit(2);
});
