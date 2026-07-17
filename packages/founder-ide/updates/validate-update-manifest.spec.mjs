// validate-update-manifest.spec.mjs
//
// Unit tests for the manifest validator's pure-logic surface. The validator
// was refactored (Phase 5 / Workstream E) to export `validateManifestObject`
// which returns `{ errors, warnings, checks }` instead of calling process.exit.
// That lets us drive every invariant from a single in-process suite without
// spawning subprocesses or writing temp JSON files to disk.
//
// Run:  node --test packages/founder-ide/updates/validate-update-manifest.spec.mjs
//
// Coverage (from the Workstream E brief):
//   - Manifest with yanked `latestVersion` fails
//   - Manifest with placeholder SHA fails (Phase 5 hardening — re-verified)
//   - Manifest with valid SHAs passes
//   - Status enum validation (unknown status fails)
// Plus extras:
//   - current release missing sha256 fails
//   - duplicate versions fail
//   - latestVersion with no matching entry fails
//   - non-legacy release below the minimumVersion floor fails
//   - latestVersion older than a release fails

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  validateManifestObject,
  isSha256,
  isPlaceholder,
  isSemver,
  semverLt,
  VALID_RELEASE_STATUSES,
} from './validate-update-manifest.mjs';

// ─── Fixtures ─────────────────────────────────────────────────────────────

const VALID_SHA = 'b5701ce07b1057ed2eab0db6cc7de24bb4e9ee390da634a6d6d89f58dd61bee5';
const OTHER_VALID_SHA = '97a01b76a246c8b02b90a9401a0427226ec5f9a667e660e56095ecda9995efa8';

function manifestWith(opts = {}) {
  const {
    latestVersion = '0.9.2',
    minimumVersion = '0.9.1',
    channel = 'stable',
    releases,
  } = opts;
  return {
    manifestVersion: 1,
    publishedAt: '2026-07-17T00:00:00Z',
    product: 'founder-stack',
    channel,
    latestVersion,
    minimumVersion,
    releaseNotesUrl: 'https://example.invalid/releases',
    releases: releases ?? [
      {
        version: '0.9.2',
        status: 'current',
        releasedAt: '2026-07-17T00:00:00Z',
        foundation: 'void',
        download: {
          url: 'https://example.invalid/0.9.2.exe',
          size: 100,
          sha256: VALID_SHA,
          signature: null,
        },
        minimumVersion: '0.9.1',
        rollback: {
          mechanism: 'nsis-upgrade-in-place',
          previousVersionKept: true,
          note: 'rollback supported',
        },
      },
    ],
  };
}

// ─── Pure-helper tests ────────────────────────────────────────────────────

describe('helpers', () => {
  test('isSemver accepts major.minor.patch', () => {
    assert.equal(isSemver('0.9.10'), true);
    assert.equal(isSemver('1.0.0'), true);
  });
  test('isSemver rejects pre-release and garbage', () => {
    assert.equal(isSemver('1.0.0-rc1'), false);
    assert.equal(isSemver('garbage'), false);
    assert.equal(isSemver(''), false);
    assert.equal(isSemver(null), false);
  });
  test('isSha256 accepts 64-hex', () => {
    assert.equal(isSha256(VALID_SHA), true);
  });
  test('isSha256 rejects short / non-hex / placeholder', () => {
    assert.equal(isSha256('abc'), false);
    assert.equal(isSha256('PLACEHOLDER_FILL_AFTER_PUBLISH_SHA256SUM'), false);
  });
  test('isPlaceholder flags PLACEHOLDER_* sentinels', () => {
    assert.equal(isPlaceholder('PLACEHOLDER_X'), true);
    assert.equal(isPlaceholder(VALID_SHA), false);
  });
  test('semverLt handles (0.9.2, 0.9.10) without lexicographic trap', () => {
    assert.equal(semverLt('0.9.2', '0.9.10'), true);
    assert.equal(semverLt('0.9.10', '0.9.2'), false);
    assert.equal(semverLt('1.0.0', '0.9.99'), false);
  });
});

// ─── Status enum ──────────────────────────────────────────────────────────

describe('status enum', () => {
  test('VALID_RELEASE_STATUSES matches the schema enum', () => {
    assert.deepEqual([...VALID_RELEASE_STATUSES].sort(),
      ['current', 'legacy', 'pending-build', 'yanked']);
  });
  test('rejects unknown status', () => {
    const m = manifestWith({
      releases: [{
        version: '0.9.2',
        status: 'beta', // not in the enum
        download: { url: 'https://x.invalid/y.exe', size: 1, sha256: VALID_SHA, signature: null },
        minimumVersion: '0.9.1',
        rollback: { mechanism: 'nsis-upgrade-in-place' },
      }],
    });
    const { errors } = validateManifestObject(m);
    assert.ok(errors.some((e) => /\[0\.9\.2\] status "beta"/.test(e)));
  });
});

// ─── Yanked latestVersion ─────────────────────────────────────────────────

describe('yanked latestVersion', () => {
  test('fails when latestVersion is yanked', () => {
    const m = manifestWith({
      latestVersion: '0.9.0',
      releases: [{
        version: '0.9.0',
        status: 'yanked',
        releasedAt: '2026-07-15T00:00:00Z',
        download: { url: 'https://x.invalid/y.exe', size: 1, sha256: OTHER_VALID_SHA, signature: null },
        minimumVersion: '0.8.0',
        rollback: {
          mechanism: 'nsis-upgrade-in-place',
          note: 'yanked because of launcher-less IDE',
        },
      }],
    });
    const { errors } = validateManifestObject(m);
    assert.ok(
      errors.some((e) => /latestVersion .*0\.9\.0.* is yanked/.test(e)),
      `expected a yanked-latestVersion error, got: ${JSON.stringify(errors)}`,
    );
  });
});

// ─── Placeholder SHA ──────────────────────────────────────────────────────

describe('placeholder SHA', () => {
  test('fails when a release sha256 is a PLACEHOLDER_*', () => {
    const m = manifestWith({
      latestVersion: '0.9.2',
      releases: [{
        version: '0.9.2',
        status: 'current',
        releasedAt: '2026-07-17T00:00:00Z',
        download: {
          url: 'https://x.invalid/y.exe',
          size: 1,
          sha256: 'PLACEHOLDER_FILL_AFTER_PUBLISH_SHA256SUM',
          signature: null,
        },
        minimumVersion: '0.9.1',
        rollback: { mechanism: 'nsis-upgrade-in-place' },
      }],
    });
    const { errors } = validateManifestObject(m);
    assert.ok(
      errors.some((e) => /\[0\.9\.2\] sha256 is a PLACEHOLDER/.test(e)),
      `expected a placeholder-sha error, got: ${JSON.stringify(errors)}`,
    );
  });
});

// ─── Valid manifest passes ────────────────────────────────────────────────

describe('valid manifest', () => {
  test('passes with 0 errors', () => {
    const m = manifestWith();
    const { errors, warnings, checks } = validateManifestObject(m);
    assert.equal(errors.length, 0, `unexpected errors: ${JSON.stringify(errors)}`);
    assert.ok(checks > 0);
    // warnings are acceptable (e.g. "no current/pending-build release"), but
    // a clean manifest with a current release should have none.
    assert.equal(warnings.length, 0);
  });

  test('passes with a legacy + a current release', () => {
    const m = manifestWith({
      latestVersion: '0.9.2',
      releases: [
        {
          version: '0.9.2',
          status: 'current',
          releasedAt: '2026-07-17T00:00:00Z',
          download: { url: 'https://x.invalid/a.exe', size: 1, sha256: VALID_SHA, signature: null },
          minimumVersion: '0.9.1',
          rollback: { mechanism: 'nsis-upgrade-in-place' },
        },
        {
          version: '0.9.1',
          status: 'legacy',
          releasedAt: '2026-07-15T00:00:00Z',
          download: { url: 'https://x.invalid/b.exe', size: 1, sha256: OTHER_VALID_SHA, signature: null },
          minimumVersion: '0.9.0',
          rollback: { mechanism: 'manual' },
        },
      ],
    });
    const { errors } = validateManifestObject(m);
    assert.equal(errors.length, 0, `unexpected errors: ${JSON.stringify(errors)}`);
  });
});

// ─── Other invariants ─────────────────────────────────────────────────────

describe('other invariants', () => {
  test('current release missing sha256 fails', () => {
    const m = manifestWith({
      releases: [{
        version: '0.9.2',
        status: 'current',
        releasedAt: '2026-07-17T00:00:00Z',
        download: { url: 'https://x.invalid/y.exe', size: 1, sha256: null, signature: null },
        minimumVersion: '0.9.1',
        rollback: { mechanism: 'nsis-upgrade-in-place' },
      }],
    });
    const { errors } = validateManifestObject(m);
    assert.ok(errors.some((e) => /status=current .* requires a digest|status=current but sha256/.test(e)));
  });

  test('duplicate versions fail', () => {
    const m = manifestWith({
      releases: [
        {
          version: '0.9.2',
          status: 'current',
          download: { url: 'https://x.invalid/a.exe', size: 1, sha256: VALID_SHA, signature: null },
          minimumVersion: '0.9.1',
          rollback: { mechanism: 'nsis-upgrade-in-place' },
        },
        {
          version: '0.9.2',
          status: 'legacy',
          download: { url: 'https://x.invalid/b.exe', size: 1, sha256: OTHER_VALID_SHA, signature: null },
          minimumVersion: '0.9.1',
          rollback: { mechanism: 'manual' },
        },
      ],
    });
    const { errors } = validateManifestObject(m);
    assert.ok(errors.some((e) => /\[0\.9\.2\] duplicate version/.test(e)));
  });

  test('latestVersion with no matching entry fails', () => {
    const m = manifestWith({
      latestVersion: '9.9.9',
      releases: [{
        version: '0.9.2',
        status: 'current',
        download: { url: 'https://x.invalid/y.exe', size: 1, sha256: VALID_SHA, signature: null },
        minimumVersion: '0.9.1',
        rollback: { mechanism: 'nsis-upgrade-in-place' },
      }],
    });
    const { errors } = validateManifestObject(m);
    assert.ok(errors.some((e) => /latestVersion \(9\.9\.9\) has no entry/.test(e)));
  });

  test('non-legacy release below minimumVersion floor fails', () => {
    const m = manifestWith({
      latestVersion: '0.9.2',
      minimumVersion: '0.9.1',
      releases: [{
        version: '0.9.2',
        status: 'current',
        download: { url: 'https://x.invalid/y.exe', size: 1, sha256: VALID_SHA, signature: null },
        minimumVersion: '0.8.0', // below floor
        rollback: { mechanism: 'nsis-upgrade-in-place' },
      }],
    });
    const { errors } = validateManifestObject(m);
    assert.ok(errors.some((e) => /below manifest floor/.test(e)));
  });

  test('latestVersion older than a release fails', () => {
    const m = manifestWith({
      latestVersion: '0.9.1',
      minimumVersion: '0.9.0',
      releases: [
        {
          version: '0.9.1',
          status: 'current',
          download: { url: 'https://x.invalid/a.exe', size: 1, sha256: VALID_SHA, signature: null },
          minimumVersion: '0.9.0',
          rollback: { mechanism: 'nsis-upgrade-in-place' },
        },
        {
          version: '0.9.2', // newer than latestVersion — invariant violation
          status: 'legacy',
          download: { url: 'https://x.invalid/b.exe', size: 1, sha256: OTHER_VALID_SHA, signature: null },
          minimumVersion: '0.9.0',
          rollback: { mechanism: 'manual' },
        },
      ],
    });
    const { errors } = validateManifestObject(m);
    assert.ok(errors.some((e) => /latestVersion 0\.9\.1 < release 0\.9\.2/.test(e)));
  });
});

// ─── On-disk manifest (smoke) ─────────────────────────────────────────────

describe('on-disk manifest', () => {
  test('the shipped manifest passes validation', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const url = await import('node:url');
    const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
    const manifestPath = path.join(__dirname, 'founder-stack-updates.json');
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    const { errors } = validateManifestObject(manifest);
    assert.equal(errors.length, 0, `shipped manifest has errors: ${JSON.stringify(errors)}`);
  });
});
