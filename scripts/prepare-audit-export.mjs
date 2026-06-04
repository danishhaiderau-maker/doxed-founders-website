/**
 * Exports a code-only snapshot for ChatGPT / external security audit.
 * No .env files, no vault, no production tokens.
 */
import fs from 'fs';
import path from 'path';
import { repoRoot, getAuditExportRoot, getSecretsVaultRoot } from './secrets-vault-path.mjs';

const DEST = getAuditExportRoot();

/** Top-level files/dirs copied into the audit bundle. */
const INCLUDE_TOP = new Set(['apps', 'packages', 'prisma', '.github', 'docs', 'services']);
const INCLUDE_ROOT_FILES = new Set(['package.json', 'tsconfig.base.json', 'AUDIT.md']);

const SKIP_DIRS = new Set([
  'node_modules',
  '.next',
  'out',
  'dist',
  'build',
  'release',
  '.turbo',
  '.git',
  '.vercel',
  'coverage',
  'docker',
  'scripts',
  'config',
  'data',
  '.selfhost-pids',
  '.dev-pids',
]);

const SKIP_FILE_PATTERNS = [
  /^\.env(\.|$)/,
  /\.env(\.|$)/,
  /^railway/,
  /^vercel\.json$/,
  /^google-keys\.txt$/,
  /\.paste\.env$/,
  /^README\.md$/i,
  /\.(log|db|db-journal)$/,
  /^agent-restart/,
  /^restart-report/,
  /^_verify-out/,
  /^mark\d*\.txt$/,
  /^deploy-cloud/,
  /^setup-.*\.cmd$/,
  /^dev-.*\.cmd$/,
  /^bootstrap-/,
  /^start-.*\.cmd$/,
  /^finish-/,
  /^check-/,
  /^fix-/,
  /^launch\d/,
  /^make_restart/,
  /^Dockerfile$/,
  /^docker-compose/,
  /^\.dockerignore$/,
  /^turbo\.json$/,
  /^package-lock\.json$/,
];

const SECRET_PATTERNS = [
  /postgresql:\/\/[^\s'"]+:[^\s'"]+@/i,
  /Bearer\s+[A-Za-z0-9._-]{30,}/,
  /TWITTER_ACCESS_TOKEN=[A-Za-z0-9_-]{20,}/,
  /JWT_SECRET=["'][^"']{20,}["']/,
  /npg_[A-Za-z0-9]+/,
  /eyJhbGciOiJIUzI1NiIsIn[^"'\s]{50,}/,
];

/** Cron workflows need scripts/ — excluded from code-only audit bundle. Run them on doxed-founders-website. */
const SKIP_AUDIT_WORKFLOWS = new Set([
  'x-social-daily.yml',
  'engagement-lottery-daily.yml',
]);

function shouldSkipFile(name, relPath) {
  if (SKIP_FILE_PATTERNS.some((re) => re.test(name))) return true;
  const norm = relPath.replace(/\\/g, '/');
  if (norm.startsWith('.github/workflows/') && SKIP_AUDIT_WORKFLOWS.has(name)) return true;
  if (/apps\/web\/vercel\.json$/.test(norm)) return true;
  if (/schema\.sqlite\.prisma$/.test(name)) return true;
  return false;
}

function copyTree(src, dest, rel = '') {
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    const base = path.basename(src);
    if (SKIP_DIRS.has(base)) return;
    if (!rel && !INCLUDE_TOP.has(base)) return;
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      copyTree(path.join(src, entry), path.join(dest, entry), rel ? `${rel}/${entry}` : entry);
    }
    return;
  }
  const base = path.basename(src);
  const relPath = rel || base;
  if (shouldSkipFile(base, relPath)) return;
  if (prismaOnlySchema(relPath)) return;
  const text = fs.readFileSync(src, 'utf8');
  for (const re of SECRET_PATTERNS) {
    if (re.test(text)) {
      console.warn(`WARN: possible secret in ${relPath} — skipped from export`);
      return;
    }
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function prismaOnlySchema(relPath) {
  const norm = relPath.replace(/\\/g, '/');
  if (!norm.startsWith('prisma/')) return false;
  return !['prisma/schema.prisma', 'prisma/seed.ts'].includes(norm);
}

function writeScopeDoc() {
  const scope = `DOXXED CRYPTO — AUDIT EXPORT (CODE ONLY)
=====================================
Generated: ${new Date().toISOString()}

START HERE (included in docs/):
  - docs/AUDIT_FOR_CHATGPT.md  — review checklist, no secrets
  - docs/MISSION.md            — product mission & Founder OS / Node / BYOK
  - docs/REPOSITORY_LAYOUT.md  — public vs private boundaries
  - AUDIT.md (repo root)       — entry pointer

This bundle contains application source only. It intentionally excludes:
- All .env and secret files (stored in ../doxedcryptofounder-secrets/)
- node_modules, build artifacts, scripts/ (production sync ops)
- Production credentials (Railway, Vercel, Neon, X OAuth)

Review focus:
1. apps/api/src — NestJS API, auth, founder-os, paper-trading, admin-control
2. apps/web/src — Next.js UI, auth-options, API client
3. prisma/schema.prisma — data model
4. packages/utils — business logic
5. services/btc-conservative-agent — showcase bot (BOT_CONTROL_SECRET on POST routes)
6. Integration credentials encrypted at rest (AES-256-GCM) — verify key handling

Security areas (2026):
- Paper trading session tokens (paper-session.util.ts)
- GitHub webhook signature required in production
- POST /projects/sync-metrics requires METRICS_SYNC_SECRET in production
- Bot POST routes require BOT_CONTROL_SECRET

Main app repo: github.com/danishhaiderau-maker/doxed-founders-website

Included: apps (api, web, founder-node), packages, prisma schema, docs, services (no runtime data/).

Scheduled GitHub Actions (X daily sync, engagement lottery) are NOT exported here.
They belong on doxed-founders-website only — that repo includes scripts/ and GitHub secrets
(API_URL, ADMIN_SYNC_JWT). Delete any *-daily.yml workflows left in the audit repo.

Do NOT ask the user to paste .env files. Flag env var NAMES only.
`;
  fs.writeFileSync(path.join(DEST, 'AUDIT_SCOPE.txt'), scope, 'utf8');
}

if (fs.existsSync(DEST)) {
  fs.rmSync(DEST, { recursive: true, force: true });
}
fs.mkdirSync(DEST, { recursive: true });

console.log(`Exporting audit bundle to:\n  ${DEST}\n`);
for (const entry of fs.readdirSync(repoRoot)) {
  const src = path.join(repoRoot, entry);
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    if (INCLUDE_TOP.has(entry)) {
      copyTree(src, path.join(DEST, entry), entry);
    }
  } else if (INCLUDE_ROOT_FILES.has(entry)) {
    copyTree(src, path.join(DEST, entry), entry);
  }
}
writeScopeDoc();

console.log(`\nAudit export ready.`);
console.log(`Secrets vault (never share): ${getSecretsVaultRoot()}`);
console.log(`Give ChatGPT: folder path or zip of ${DEST} + AUDIT_SCOPE.txt`);
