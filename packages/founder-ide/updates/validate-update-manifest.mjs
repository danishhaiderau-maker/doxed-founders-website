#!/usr/bin/env node
// validate-update-manifest.mjs
//
// Validates packages/founder-ide/updates/founder-stack-updates.json against the
// invariants that the update-detection + rollback path relies on. Exits non-zero
// on any violation so it can gate a publish.
//
// Run:  node packages/founder-ide/updates/validate-update-manifest.mjs

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MANIFEST = path.join(__dirname, 'founder-stack-updates.json');
const SCHEMA = path.join(__dirname, 'founder-stack-updates.schema.json');

const errors = [];
const warnings = [];
let checks = 0;

function ok(msg) { checks++; console.log(`  \u2713 ${msg}`); }
function fail(msg) { errors.push(msg); console.error(`  \u2717 ${msg}`); }
function warn(msg) { warnings.push(msg); console.warn(`  ! ${msg}`); }

function isSemver(s) { return /^\d+\.\d+\.\d+$/.test(s); }

function isSha256(s) {
  return typeof s === 'string' && /^[a-f0-9]{64}$/i.test(s);
}

function isPlaceholder(s) {
  return typeof s === 'string' && s.startsWith('PLACEHOLDER_');
}

console.log('Validating Founder Stack update manifest\n');

// --- Load + parse -----------------------------------------------------------
if (!fs.existsSync(MANIFEST)) {
  console.error(`FATAL: manifest not found at ${MANIFEST}`);
  process.exit(2);
}
if (!fs.existsSync(SCHEMA)) {
  console.error(`FATAL: schema not found at ${SCHEMA}`);
  process.exit(2);
}

let manifest;
try {
  manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  ok(`manifest parses as JSON (${path.basename(MANIFEST)})`);
} catch (e) {
  console.error(`FATAL: manifest is not valid JSON: ${e.message}`);
  process.exit(2);
}

// --- Structural checks ------------------------------------------------------
checks++;
if (manifest.manifestVersion === 1) ok('manifestVersion == 1');
else fail(`manifestVersion must be 1 (got ${manifest.manifestVersion})`);

checks++;
if (manifest.product === 'founder-stack') ok('product == "founder-stack"');
else fail(`product must be "founder-stack" (got ${manifest.product})`);

checks++;
if (isSemver(manifest.latestVersion)) ok(`latestVersion is semver (${manifest.latestVersion})`);
else fail(`latestVersion is not semver: ${manifest.latestVersion}`);

checks++;
if (isSemver(manifest.minimumVersion)) ok(`minimumVersion is semver (${manifest.minimumVersion})`);
else fail(`minimumVersion is not semver: ${manifest.minimumVersion}`);

checks++;
if (Array.isArray(manifest.releases) && manifest.releases.length > 0) {
  ok(`releases has ${manifest.releases.length} entr${manifest.releases.length === 1 ? 'y' : 'ies'}`);
} else {
  fail('releases must be a non-empty array');
}

// --- Per-release checks -----------------------------------------------------
const seenVersions = new Set();
for (const r of manifest.releases ?? []) {
  console.log(`\nRelease ${r.version} (${r.status ?? '?'})`);

  checks++;
  if (!isSemver(r.version)) { fail(`[${r.version}] version is not semver`); }
  else ok(`[${r.version}] version is semver`);

  checks++;
  if (seenVersions.has(r.version)) fail(`[${r.version}] duplicate version in releases`);
  else seenVersions.add(r.version);

  checks++;
  if (!r.status) { fail(`[${r.version}] missing status`); }
  else ok(`[${r.version}] status = ${r.status}`);

  checks++;
  if (!isSemver(r.minimumVersion)) { fail(`[${r.version}] minimumVersion not semver`); }
  else ok(`[${r.version}] minimumVersion = ${r.minimumVersion}`);

  // download
  const dl = r.download ?? {};
  checks++;
  if (!dl.url || !/^https?:\/\//.test(dl.url)) { fail(`[${r.version}] download.url missing or not http(s)`); }
  else ok(`[${r.version}] download.url present`);

  checks++;
  if (isSha256(dl.sha256)) {
    ok(`[${r.version}] sha256 is a valid 64-hex digest`);
  } else if (isPlaceholder(dl.sha256)) {
    warn(`[${r.version}] sha256 is a PLACEHOLDER — must be filled before publish`);
  } else if (dl.sha256 == null && r.status === 'pending-build') {
    ok(`[${r.version}] sha256 null + status=pending-build (acceptable pre-build)`);
  } else {
    fail(`[${r.version}] sha256 invalid: ${dl.sha256}`);
  }

  // rollback strategy declared
  checks++;
  if (!r.rollback || !r.rollback.mechanism) {
    fail(`[${r.version}] missing rollback.mechanism`);
  } else {
    ok(`[${r.version}] rollback mechanism = ${r.rollback.mechanism}`);
  }
}

// --- Cross-release invariants ----------------------------------------------
console.log('\nCross-release invariants');

// latestVersion must have a matching release entry.
checks++;
const latestEntry = (manifest.releases ?? []).find((r) => r.version === manifest.latestVersion);
if (!latestEntry) fail(`latestVersion (${manifest.latestVersion}) has no entry in releases[]`);
else ok(`latestVersion (${manifest.latestVersion}) exists in releases[]`);

// No release may advertise a minimumVersion below the manifest floor unless it
// is marked legacy (legacy releases are allowed to predate the floor).
checks++;
for (const r of manifest.releases ?? []) {
  if (r.status === 'legacy') continue;
  if (semverLt(r.minimumVersion, manifest.minimumVersion)) {
    fail(`[${r.version}] minimumVersion ${r.minimumVersion} below manifest floor ${manifest.minimumVersion}`);
  }
}
ok('non-legacy releases respect the manifest minimumVersion floor');

// Exactly one release should be the advertised current/pending-build target.
checks++;
const currentish = (manifest.releases ?? []).filter((r) => r.status === 'current' || r.status === 'pending-build');
if (currentish.length === 1) {
  ok(`exactly one current/pending-build release (${currentish[0].version})`);
} else if (currentish.length === 0) {
  warn('no current/pending-build release — manifest describes only legacy/yanked releases');
} else {
  fail(`expected exactly one current/pending-build release, found ${currentish.length}: ${currentish.map((r) => r.version).join(', ')}`);
}

// latestVersion must be >= every release version.
checks++;
for (const r of manifest.releases ?? []) {
  if (semverLt(manifest.latestVersion, r.version)) {
    fail(`latestVersion ${manifest.latestVersion} < release ${r.version}`);
  }
}
ok('latestVersion >= all release versions');

// --- Report -----------------------------------------------------------------
console.log(`\n${checks} check(s), ${warnings.length} warning(s), ${errors.length} error(s)\n`);
if (errors.length > 0) {
  console.error('RESULT: FAIL');
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}
console.log('RESULT: PASS');
process.exit(0);

// Minimal semver lt (a < b) — no pre-release tags needed for this manifest.
function semverLt(a, b) {
  const [aM, am, ap] = a.split('.').map(Number);
  const [bM, bm, bp] = b.split('.').map(Number);
  if (aM !== bM) return aM < bM;
  if (am !== bm) return am < bm;
  return ap < bp;
}
