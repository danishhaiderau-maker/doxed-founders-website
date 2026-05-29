/**
 * Exports a code-only snapshot for ChatGPT / external security audit.
 * No .env files, no vault, no production tokens.
 */
import fs from 'fs';
import path from 'path';
import { repoRoot, getAuditExportRoot, getSecretsVaultRoot } from './secrets-vault-path.mjs';

const DEST = getAuditExportRoot();

/** Top-level files/dirs copied into the audit bundle. */
const INCLUDE_TOP = new Set(['apps', 'packages', 'prisma', '.github']);
const INCLUDE_ROOT_FILES = new Set(['package.json', 'tsconfig.base.json']);

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
  'docker',
  'scripts',
  'config',
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

function shouldSkipFile(name, relPath) {
  if (SKIP_FILE_PATTERNS.some((re) => re.test(name))) return true;
  if (/apps\/web\/vercel\.json$/.test(relPath.replace(/\\/g, '/'))) return true;
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

This bundle contains application source only. It intentionally excludes:
- All .env and secret files (stored in ../doxedcryptofounder-secrets/)
- node_modules, build artifacts
- Production credentials (Railway, Vercel, Neon, X OAuth)

Review focus:
1. apps/api/src — NestJS API, auth, founder-os, builder, events, founder-den
2. apps/web/src — Next.js UI, auth-options, API client
3. prisma/schema.prisma — data model (Phase 6 Raise Room, Phase 7 Scout Markets / Founder Brain)
4. packages/utils — business logic (ai-providers desk workflow, scout-markets, founder-brain)
5. Integration credentials encrypted at rest (AES-256-GCM) — verify key handling

Phases in this export:
- Phase 6: Raise Room (1% allocation burn, participant export, EVM wallet verify)
- Phase 7: Scout prediction markets, Founder Brain Q&A, desk AI providers (Cursor, Claude Code, Codex, Windsurf, OpenHands, OpenClaw)

Public audit repo: github.com/danishhaiderau-maker/doxed-founders-audit
Main app repo: github.com/danishhaiderau-maker/doxed-founders-website

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
