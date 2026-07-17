#!/usr/bin/env node
// validate-update-manifest.mjs
//
// Validates packages/founder-ide/updates/founder-stack-updates.json against the
// invariants that the update-detection + rollback path relies on. Exits non-zero
// on any violation so it can gate a publish.
//
// Run:  node packages/founder-ide/updates/validate-update-manifest.mjs
//
// The validator logic is exported as `validateManifestObject` (returns a
// `{ errors, warnings, checks }` shape) so the test suite can drive it
// without spawning a subprocess. The CLI shim below wires the function to
// the on-disk manifest and prints the same verbose per-check trace that the
// Phase 5 hardening commit (ab8f3796) established.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// ─── Helpers (exported for tests) ─────────────────────────────────────────

/** True if `s` looks like major.minor.patch (no pre-release tags). */
export function isSemver(s) {
  return typeof s === 'string' && /^\d+\.\d+\.\d+$/.test(s);
}

/** True if `s` is a 64-char lowercase-or-uppercase hex SHA-256 digest. */
export function isSha256(s) {
  return typeof s === 'string' && /^[a-f0-9]{64}$/i.test(s);
}

/** True if `s` is a PLACEHOLDER_* sentinel value that must be filled before publish. */
export function isPlaceholder(s) {
  return typeof s === 'string' && s.startsWith('PLACEHOLDER_');
}

/** Minimal semver lt (a < b). No pre-release tags needed for this manifest. */
export function semverLt(a, b) {
  const [aM, am, ap] = a.split('.').map(Number);
  const [bM, bm, bp] = b.split('.').map(Number);
  if (aM !== bM) return aM < bM;
  if (am !== bm) return am < bm;
  return ap < bp;
}

export const VALID_RELEASE_STATUSES = ['current', 'pending-build', 'legacy', 'yanked'];

/**
 * Run every invariant against an in-memory manifest object. Returns a result
 * shape so callers (tests, CI) can introspect without parsing stdout.
 *
 * Each `check` is a single test that contributes to the count. The optional
 * `log` callback (CLI uses console.log/error/warn) lets the CLI mirror the
 * verbose Phase 5 output without this function knowing about stdout.
 *
 * @param {unknown} manifestInput
 * @param {{ ok?: (m: string) => void, fail?: (m: string) => void, warn?: (m: string) => void, section?: (m: string) => void }} [logger]
 * @returns {{ errors: string[], warnings: string[], checks: number }}
 */
export function validateManifestObject(manifestInput, logger = {}) {
  const errors = [];
  const warnings = [];
  let checks = 0;

  const ok = (m) => { logger.ok?.(m); };
  const fail = (m) => { errors.push(m); logger.fail?.(m); };
  const warn = (m) => { warnings.push(m); logger.warn?.(m); };

  const manifest = /** @type {any} */ (manifestInput);

  if (!manifest || typeof manifest !== 'object') {
    errors.push('manifest is not an object');
    return { errors, warnings, checks };
  }

  // --- Structural checks ----------------------------------------------------
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

  // --- Per-release checks ---------------------------------------------------
  const seenVersions = new Set();
  for (const r of manifest.releases ?? []) {
    logger.section?.(`\nRelease ${r.version} (${r.status ?? '?'})`);

    checks++;
    if (!isSemver(r.version)) fail(`[${r.version}] version is not semver`);
    else ok(`[${r.version}] version is semver`);

    checks++;
    if (seenVersions.has(r.version)) fail(`[${r.version}] duplicate version in releases`);
    else seenVersions.add(r.version);

    checks++;
    if (!r.status) fail(`[${r.version}] missing status`);
    else if (!VALID_RELEASE_STATUSES.includes(r.status)) {
      fail(`[${r.version}] status "${r.status}" is not one of ${VALID_RELEASE_STATUSES.join(', ')}`);
    } else ok(`[${r.version}] status = ${r.status}`);

    checks++;
    if (!isSemver(r.minimumVersion)) fail(`[${r.version}] minimumVersion not semver`);
    else ok(`[${r.version}] minimumVersion = ${r.minimumVersion}`);

    const dl = r.download ?? {};
    checks++;
    if (!dl.url || !/^https?:\/\//.test(dl.url)) fail(`[${r.version}] download.url missing or not http(s)`);
    else ok(`[${r.version}] download.url present`);

    checks++;
    if (isSha256(dl.sha256)) {
      ok(`[${r.version}] sha256 is a valid 64-hex digest`);
    } else if (dl.sha256 == null && r.status === 'pending-build') {
      ok(`[${r.version}] sha256 null + status=pending-build (acceptable pre-build)`);
    } else if (isPlaceholder(dl.sha256)) {
      // PLACEHOLDER_* must FAIL validation, not warn. A placeholder that
      // reaches a published manifest means the updater would happily download
      // a binary it cannot integrity-check — that is a security regression.
      fail(`[${r.version}] sha256 is a PLACEHOLDER — must be replaced with the real 64-hex digest before publish`);
    } else if (dl.sha256 == null && (r.status === 'current' || r.status === 'legacy')) {
      fail(`[${r.version}] sha256 is null but status=${r.status} requires a digest (only pending-build may omit)`);
    } else {
      fail(`[${r.version}] sha256 invalid: ${dl.sha256}`);
    }

    // status-specific integrity
    checks++;
    if (r.status === 'yanked') {
      if (typeof r.rollback?.note === 'string' && r.rollback.note.length > 0) {
        ok(`[${r.version}] yanked release has a rollback note explaining why`);
      } else {
        warn(`[${r.version}] yanked release has no rollback.note — users won't see why it was pulled`);
      }
    } else if (r.status === 'current') {
      if (!isSha256(dl.sha256)) {
        fail(`[${r.version}] status=current but sha256 is not a real digest — cannot ship`);
      } else {
        ok(`[${r.version}] status=current has a real sha256 digest`);
      }
    }

    checks++;
    if (!r.rollback || !r.rollback.mechanism) {
      fail(`[${r.version}] missing rollback.mechanism`);
    } else {
      ok(`[${r.version}] rollback mechanism = ${r.rollback.mechanism}`);
    }
  }

  // --- Cross-release invariants --------------------------------------------
  logger.section?.('\nCross-release invariants');

  checks++;
  const latestEntry = (manifest.releases ?? []).find((r) => r.version === manifest.latestVersion);
  if (!latestEntry) fail(`latestVersion (${manifest.latestVersion}) has no entry in releases[]`);
  else ok(`latestVersion (${manifest.latestVersion}) exists in releases[]`);

  checks++;
  let floorFailureCount = 0;
  for (const r of manifest.releases ?? []) {
    if (r.status === 'legacy' || r.status === 'yanked') continue;
    if (typeof r.minimumVersion === 'string' && typeof manifest.minimumVersion === 'string' &&
        semverLt(r.minimumVersion, manifest.minimumVersion)) {
      fail(`[${r.version}] minimumVersion ${r.minimumVersion} below manifest floor ${manifest.minimumVersion}`);
      floorFailureCount++;
    }
  }
  if (floorFailureCount === 0) ok('non-legacy/yanked releases respect the manifest minimumVersion floor');

  checks++;
  if (latestEntry && latestEntry.status === 'yanked') {
    fail(`latestVersion (${manifest.latestVersion}) is yanked — must point at a current/pending-build release`);
  } else {
    ok('latestVersion is not yanked');
  }

  checks++;
  const currentish = (manifest.releases ?? []).filter((r) => r.status === 'current' || r.status === 'pending-build');
  if (currentish.length === 1) {
    ok(`exactly one current/pending-build release (${currentish[0].version})`);
  } else if (currentish.length === 0) {
    warn('no current/pending-build release — manifest describes only legacy/yanked releases');
  } else {
    fail(`expected exactly one current/pending-build release, found ${currentish.length}: ${currentish.map((r) => r.version).join(', ')}`);
  }

  checks++;
  let ltLatestFailureCount = 0;
  for (const r of manifest.releases ?? []) {
    if (typeof r.version === 'string' && typeof manifest.latestVersion === 'string' &&
        semverLt(manifest.latestVersion, r.version)) {
      fail(`latestVersion ${manifest.latestVersion} < release ${r.version}`);
      ltLatestFailureCount++;
    }
  }
  if (ltLatestFailureCount === 0) ok('latestVersion >= all release versions');

  return { errors, warnings, checks };
}

// ─── CLI shim (only runs when invoked directly) ───────────────────────────

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MANIFEST = path.join(__dirname, 'founder-stack-updates.json');

const invokedDirectly =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  console.log('Validating Founder Stack update manifest\n');
  if (!fs.existsSync(MANIFEST)) {
    console.error(`FATAL: manifest not found at ${MANIFEST}`);
    process.exit(2);
  }
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
    console.log(`  \u2713 manifest parses as JSON (${path.basename(MANIFEST)})`);
  } catch (e) {
    console.error(`FATAL: manifest is not valid JSON: ${e.message}`);
    process.exit(2);
  }

  const { errors, warnings, checks } = validateManifestObject(manifest, {
    ok: (m) => console.log(`  \u2713 ${m}`),
    fail: (m) => console.error(`  \u2717 ${m}`),
    warn: (m) => console.warn(`  ! ${m}`),
    section: (m) => console.log(m),
  });

  console.log(`\n${checks} check(s), ${warnings.length} warning(s), ${errors.length} error(s)\n`);
  if (errors.length > 0) {
    console.error('RESULT: FAIL');
    for (const e of errors) console.error(`  - ${e}`);
    process.exit(1);
  }
  console.log('RESULT: PASS');
  process.exit(0);
}
