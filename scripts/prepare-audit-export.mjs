/**
 * Exports a code-only snapshot for ChatGPT / external security audit.
 * No .env files, no vault, no production tokens.
 */
import fs from 'fs';
import path from 'path';
import { repoRoot, getAuditExportRoot, getSecretsVaultRoot } from './secrets-vault-path.mjs';

const DEST = getAuditExportRoot();

const SKIP_DIRS = new Set([
  'node_modules',
  '.next',
  'out',
  'dist',
  'build',
  '.turbo',
  '.git',
  '.vercel',
  'coverage',
  'docker/data',
  '.selfhost-pids',
  '.dev-pids',
]);

const SKIP_FILE_PATTERNS = [
  /^\.env(\.|$)/,
  /^railway-x-paste\.env$/,
  /^google-keys\.txt$/,
  /\.paste\.env$/,
  /^\.env\.vercel\./,
  /^\.env\.admin/,
  /^\.env\.neon$/,
  /^\.env\.self-host$/,
  /^\.env\.tunnel/,
  /^\.env\.prod\.rotate$/,
];

const SECRET_PATTERNS = [
  /postgresql:\/\/[^\s'"]+:[^\s'"]+@/i,
  /Bearer\s+[A-Za-z0-9._-]{30,}/,
  /TWITTER_ACCESS_TOKEN=[A-Za-z0-9_-]{20,}/,
  /JWT_SECRET=["'][^"']{20,}["']/,
  /npg_[A-Za-z0-9]+/,
  /eyJhbGciOiJIUzI1NiIsIn[^"'\s]{50,}/,
];

function shouldSkipFile(name) {
  if (name.endsWith('.example')) return false;
  if (name === 'railway-x-vars.template.env') return false;
  return SKIP_FILE_PATTERNS.some((re) => re.test(name));
}

function copyTree(src, dest) {
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    const base = path.basename(src);
    if (SKIP_DIRS.has(base)) return;
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      copyTree(path.join(src, entry), path.join(dest, entry));
    }
    return;
  }
  const base = path.basename(src);
  if (shouldSkipFile(base)) return;
  const isExample = base.includes('.example') || base.includes('template');
  if (!isExample) {
    const text = fs.readFileSync(src, 'utf8');
    for (const re of SECRET_PATTERNS) {
      if (re.test(text)) {
        console.warn(`WARN: possible secret in ${path.relative(repoRoot, src)} — skipped from export`);
        return;
      }
    }
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function writeScopeDoc() {
  const scope = `DOXXED CRYPTO — AUDIT EXPORT (CODE ONLY)
=====================================
Generated: ${new Date().toISOString()}

This bundle contains application source only. It intentionally excludes:
- All .env and secret files (stored in ../doxedcryptofounder-secrets/)
- node_modules, build artifacts
- Production credentials (Railway, Vercel, Neon, X OAuth)

Review focus:
1. apps/api/src — NestJS API, auth, founder-os, conviction-share, engagement-rewards
2. apps/web/src — Next.js UI, auth-options, API client
3. prisma/schema.prisma — data model (OAuth tokens stored encrypted-at-app-level: NO — flag this)
4. packages/utils — business logic
5. scripts — automation (no env contents included)

Public GitHub (optional): github.com/danishhaiderau-maker/doxed-founders-website
Make repo public only after confirming no secrets in git history.

Do NOT ask the user to paste .env files. Flag env var NAMES only.
`;
  fs.writeFileSync(path.join(DEST, 'AUDIT_SCOPE.txt'), scope, 'utf8');
}

if (fs.existsSync(DEST)) {
  fs.rmSync(DEST, { recursive: true, force: true });
}
fs.mkdirSync(DEST, { recursive: true });

console.log(`Exporting audit bundle to:\n  ${DEST}\n`);
copyTree(repoRoot, DEST);
writeScopeDoc();

console.log(`\nAudit export ready.`);
console.log(`Secrets vault (never share): ${getSecretsVaultRoot()}`);
console.log(`Give ChatGPT: folder path or zip of ${DEST} + AUDIT_SCOPE.txt`);
