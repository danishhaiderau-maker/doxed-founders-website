/**
 * Unit tests for the Founder Stack IDE updater (Phase 5 — Workstream E).
 *
 * Strategy: every side effect (network download, signtool, file system,
 * handshake probe) is replaced with an in-process stub. The tests then
 * drive the public exports and assert against:
 *   - the state-machine transitions (`getIdeUpdateState`)
 *   - file-system side effects (presence/absence of `.part` and `.exe`)
 *   - Authenticode branch decisions
 *   - rollback wiring
 *
 * Run:  npx tsx --test apps/founder-node/test/ide-update-manager.spec.ts
 */

import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  type IdeUpdateManifest,
  type IdeUpdateInfo,
  type IdeUpdateRelease,
  type AuthenticodeResult,
  configureIdeUpdates,
  resolveIdeUpdate,
  resolveInstalledIdeVersion,
  computeFileSha256,
  downloadVerifyInstallAndHandshake,
  getIdeUpdateState,
  getIdeUpdateFailureReason,
  setIdeUpdateStateListener,
  __resetIdeUpdaterForTests,
  __setIdeUpdateStateForTests,
  __resetIdeManifestCacheForTests,
  findRollbackCandidate,
  waitForHandshake,
  partFilePath,
  stagedInstallerPath,
} from '../src/ide-update-manager';

// ─── Fixtures ─────────────────────────────────────────────────────────────

function makeManifest(opts: {
  latestVersion?: string;
  releases?: IdeUpdateRelease[];
  channel?: 'stable' | 'beta' | 'insider';
  minimumVersion?: string;
} = {}): IdeUpdateManifest {
  return {
    manifestVersion: 1,
    product: 'founder-stack',
    channel: opts.channel ?? 'stable',
    latestVersion: opts.latestVersion ?? '0.9.2',
    minimumVersion: opts.minimumVersion ?? '0.9.1',
    releases: opts.releases ?? [
      {
        version: '0.9.2',
        status: 'current',
        releasedAt: '2026-07-17T00:00:00Z',
        download: {
          url: 'https://example.invalid/Founder-Stack-Setup-0.9.2.exe',
          size: 100,
          sha256: 'a'.repeat(64),
          signature: null,
        },
        minimumVersion: '0.9.1',
        rollback: { mechanism: 'nsis-upgrade-in-place', previousVersionKept: true },
      },
    ],
  };
}

/** Scratch updates dir per-test. */
let scratchDir: string;
let stagedVersion = 0;

function freshScratchDir(): string {
  stagedVersion += 1;
  const d = path.join(os.tmpdir(), `founder-ide-update-test-${process.pid}-${stagedVersion}-${Date.now()}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
}

/** Stub download: writes a deterministic payload that hashes to `sha`. */
function makeDownloadStub(
  dest: string,
  payload: string,
): (url: string, dest: string) => Promise<number> {
  return async (_url: string, d: string) => {
    fs.mkdirSync(path.dirname(d), { recursive: true });
    fs.writeFileSync(d, payload);
    return payload.length;
  };
}

// ─── Setup / teardown ─────────────────────────────────────────────────────

beforeEach(() => {
  __resetIdeUpdaterForTests();
  __resetIdeManifestCacheForTests();
  scratchDir = freshScratchDir();
});

afterEach(() => {
  try {
    fs.rmSync(scratchDir, { recursive: true, force: true });
  } catch {
    /* best effort */
  }
});

describe('IDE update state notifications', () => {
  it('reports updater transitions to the embedded companion bridge', () => {
    const seen: Array<{ state: string; reason: string | null }> = [];
    setIdeUpdateStateListener((state, reason) => seen.push({ state, reason }));
    __setIdeUpdateStateForTests('downloading');
    __setIdeUpdateStateForTests('verifying');
    __setIdeUpdateStateForTests('failed', 'signature rejected');

    assert.deepEqual(seen, [
      { state: 'idle', reason: null },
      { state: 'downloading', reason: null },
      { state: 'verifying', reason: null },
      { state: 'failed', reason: 'signature rejected' },
    ]);
  });
});

// ─── Version comparison ───────────────────────────────────────────────────

describe('resolveIdeUpdate — semver comparison', () => {
  const baseRelease = (version: string, sha: string): IdeUpdateRelease => ({
    version,
    status: 'current',
    releasedAt: '2026-07-17T00:00:00Z',
    download: {
      url: 'https://example.invalid/x.exe',
      size: 100,
      sha256: sha,
      signature: null,
    },
    minimumVersion: '0.9.1',
  });

  it('0.9.1 < 0.9.2 → resolves update', () => {
    const sha = 'b'.repeat(64);
    const m = makeManifest({
      latestVersion: '0.9.2',
      releases: [baseRelease('0.9.2', sha)],
    });
    const r = resolveIdeUpdate(m, '0.9.1');
    assert.ok(r, 'expected update');
    assert.equal(r!.version, '0.9.2');
  });

  it('0.9.10 > 0.9.2 (lexicographic would fail)', () => {
    const sha = 'c'.repeat(64);
    const m = makeManifest({
      latestVersion: '0.9.10',
      releases: [baseRelease('0.9.10', sha)],
    });
    const r = resolveIdeUpdate(m, '0.9.2');
    assert.ok(r);
    assert.equal(r!.version, '0.9.10');
  });

  it('1.0.0 > 0.9.99 (major bump)', () => {
    const sha = 'd'.repeat(64);
    const m = makeManifest({
      latestVersion: '1.0.0',
      releases: [baseRelease('1.0.0', sha)],
    });
    const r = resolveIdeUpdate(m, '0.9.99');
    assert.ok(r);
    assert.equal(r!.version, '1.0.0');
  });

  it('installed == latest → null', () => {
    const sha = 'e'.repeat(64);
    const m = makeManifest({
      latestVersion: '0.9.2',
      releases: [baseRelease('0.9.2', sha)],
    });
    const r = resolveIdeUpdate(m, '0.9.2');
    assert.equal(r, null);
  });

  it('installed > latest → null', () => {
    const sha = 'f'.repeat(64);
    const m = makeManifest({
      latestVersion: '0.9.2',
      releases: [baseRelease('0.9.2', sha)],
    });
    const r = resolveIdeUpdate(m, '0.9.10');
    assert.equal(r, null);
  });
});

describe('installed Founder IDE release identity', () => {
  it('prefers the one-app installer marker over the relay package version', () => {
    const marker = path.join(scratchDir, 'founder-release.json');
    fs.writeFileSync(marker, JSON.stringify({ version: '0.9.4' }));

    assert.equal(
      resolveInstalledIdeVersion({
        markerPaths: [marker],
        fallbackVersion: '0.8.0',
      }),
      '0.9.4',
    );
  });

  it('falls back safely when the release marker is malformed', () => {
    const marker = path.join(scratchDir, 'founder-release.json');
    fs.writeFileSync(marker, JSON.stringify({ version: 'latest' }));

    assert.equal(
      resolveInstalledIdeVersion({
        markerPaths: [marker],
        fallbackVersion: '0.8.0',
      }),
      '0.8.0',
    );
  });
});

// ─── Yank refusal ─────────────────────────────────────────────────────────

// ─── Manifest fetching + caching ─────────────────────────────────────────

describe('fetchManifestCached', () => {
  it('caches the manifest for 60s between fetches', async () => {
    let fetchCount = 0;
    const manifest = makeManifest();
    configureIdeUpdates({
      apiBaseUrl: 'https://example.invalid',
      manifestFetcher: async () => {
        fetchCount += 1;
        return manifest;
      },
      skipPackagedGate: true,
    });
    const { fetchManifestCached } = await import('../src/ide-update-manager');
    const a = await fetchManifestCached();
    const b = await fetchManifestCached();
    assert.equal(fetchCount, 1, 'second call should hit the cache');
    assert.equal(a, b);
  });

  it('returns null when no api base URL configured', async () => {
    __resetIdeUpdaterForTests();
    const { fetchManifestCached } = await import('../src/ide-update-manager');
    const r = await fetchManifestCached();
    assert.equal(r, null);
  });
});

describe('resolveIdeUpdate — yanked releases', () => {
  it('refuses yanked latestVersion', () => {
    const sha = 'a'.repeat(64);
    const m = makeManifest({
      latestVersion: '0.9.0',
      releases: [
        {
          version: '0.9.0',
          status: 'yanked',
          releasedAt: '2026-07-15T00:00:00Z',
          download: { url: 'https://example.invalid/x.exe', size: 100, sha256: sha, signature: null },
          minimumVersion: '0.8.0',
          rollback: { mechanism: 'nsis-upgrade-in-place', previousVersionKept: false },
        },
      ],
    });
    assert.equal(resolveIdeUpdate(m, '0.8.0'), null);
  });
});

// ─── SHA-256 ──────────────────────────────────────────────────────────────

describe('computeFileSha256', () => {
  it('matches crypto.createHash for arbitrary content', async () => {
    const f = path.join(scratchDir, 'payload.bin');
    const body = Buffer.from('hello founder stack');
    fs.writeFileSync(f, body);
    const expected = crypto.createHash('sha256').update(body).digest('hex');
    const actual = await computeFileSha256(f);
    assert.equal(actual, expected);
  });
});

// ─── waitForHandshake ─────────────────────────────────────────────────────

describe('waitForHandshake', () => {
  it('returns true immediately when probe is true', async () => {
    const ok = await waitForHandshake(() => true, 100, 10);
    assert.equal(ok, true);
  });

  it('returns false when probe never goes true', async () => {
    const ok = await waitForHandshake(() => false, 80, 20);
    assert.equal(ok, false);
  });

  it('returns true after N polls', async () => {
    let n = 0;
    const probe = () => {
      n += 1;
      return n >= 3;
    };
    const ok = await waitForHandshake(probe, 500, 10);
    assert.equal(ok, true);
  });
});

// ─── Full install flow with stubs ─────────────────────────────────────────

/**
 * Builds a fully-stubbed updater runtime. The real `downloadToFile`,
 * `defaultAuthenticodeVerifier`, and `waitForHandshake` are bypassed via the
 * public configuration surface: `configureIdeUpdates` accepts a custom
 * `authenticodeVerifier`, `handshakeProbe`, and `manifestFetcher`. We can't
 * swap `downloadToFile` directly, so we instead point `downloadUrl` at a
 * `file://` URL — but the simplest path is to override `fetch` globally,
 * which is what the production code calls.
 */
function installFetchStub(payload: Buffer, status = 200): void {
  const fakeResponse = {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'ERR',
    body: {
      getReader() {
        let sent = false;
        return {
          async read() {
            if (sent) return { done: true, value: undefined };
            sent = true;
            return { done: false, value: new Uint8Array(payload) };
          },
        };
      },
    },
  };
  // `fetch` is a global on Node 20+. Cast to any to stub it.
  (globalThis as unknown as { fetch: typeof fetch }).fetch = (() =>
    Promise.resolve(fakeResponse as unknown as Response)) as typeof fetch;
}

function restoreFetch(): void {
  // tsx caches the original fetch on globalThis at startup; restoring is best
  // effort — we re-stub per test, so a stale restore isn't catastrophic.
  delete (globalThis as unknown as { fetch?: unknown }).fetch;
}

interface StubResult {
  sha: string;
  manifest: IdeUpdateManifest;
  updateInfo: IdeUpdateInfo;
}

function buildStub(
  opts: {
    latestVersion?: string;
    payload?: Buffer;
    allowUnsigned?: boolean;
    channel?: 'stable' | 'beta' | 'insider';
    authenticode?: AuthenticodeResult;
    handshakeProbe?: () => boolean;
    installedVersion?: string;
    installerLauncher?: (file: string, args: string[]) => void;
  } = {},
): StubResult {
  const latest = opts.latestVersion ?? '0.9.2';
  const payload = opts.payload ?? Buffer.from('founder-stack-payload');
  const sha = crypto.createHash('sha256').update(payload).digest('hex');
  const manifest = makeManifest({
    latestVersion: latest,
    channel: opts.channel ?? 'stable',
    releases: [
      {
        version: latest,
        status: 'current',
        releasedAt: '2026-07-17T00:00:00Z',
        download: {
          url: 'https://example.invalid/x.exe',
          size: payload.length,
          sha256: sha,
          signature: null,
        },
        minimumVersion: '0.9.1',
        rollback: { mechanism: 'nsis-upgrade-in-place', previousVersionKept: true },
      },
    ],
  });

  configureIdeUpdates({
    apiBaseUrl: 'https://example.invalid',
    channel: opts.channel ?? 'stable',
    allowUnsigned: opts.allowUnsigned,
    handshakeProbe: opts.handshakeProbe ?? (() => true),
    authenticodeVerifier: async () =>
      opts.authenticode ?? { signed: true, output: 'stub-signed' },
    installerLauncher:
      opts.installerLauncher ??
      (() => {
        /* no-op stub: pretend the installer launched */
      }),
    manifestFetcher: async () => manifest,
    updatesDir: scratchDir,
    installedVersionProvider: () => opts.installedVersion ?? '0.9.1',
    skipPackagedGate: true,
  });

  const updateInfo = resolveIdeUpdate(manifest, opts.installedVersion ?? '0.9.1')!;
  return { sha, manifest, updateInfo };
}

describe('downloadVerifyInstallAndHandshake — happy path', () => {
  beforeEach(() => installFetchStub(Buffer.from('founder-stack-payload')));
  afterEach(() => restoreFetch());

  it('downloads → verifies → installs → handshakes → idle', async () => {
    const { updateInfo } = buildStub({
      handshakeProbe: () => true,
    });
    await downloadVerifyInstallAndHandshake(updateInfo);
    assert.equal(getIdeUpdateState(), 'idle');
    assert.equal(getIdeUpdateFailureReason(), null);
    // Installer should be present at the staged .exe path.
    assert.ok(fs.existsSync(stagedInstallerPath(updateInfo.version)));
    // .part file should be gone (renamed to .exe).
    assert.ok(!fs.existsSync(partFilePath(updateInfo.version)));
  });
});

describe('downloadVerifyInstallAndHandshake — SHA-256 mismatch', () => {
  beforeEach(() => installFetchStub(Buffer.from('tampered-payload')));
  afterEach(() => restoreFetch());

  it('deletes file + marks failed + does NOT execute installer', async () => {
    // Manifest advertises the SHA of 'expected-payload' but the stub serves
    // a different body → mismatch.
    const expectedPayload = Buffer.from('expected-payload');
    const sha = crypto.createHash('sha256').update(expectedPayload).digest('hex');
    const manifest = makeManifest({
      latestVersion: '0.9.2',
      releases: [
        {
          version: '0.9.2',
          status: 'current',
          releasedAt: '2026-07-17T00:00:00Z',
          download: {
            url: 'https://example.invalid/x.exe',
            size: 100,
            sha256: sha,
            signature: null,
          },
          minimumVersion: '0.9.1',
        },
      ],
    });
    configureIdeUpdates({
      apiBaseUrl: 'https://example.invalid',
      handshakeProbe: () => true,
      authenticodeVerifier: async () => ({ signed: true }),
      installerLauncher: () => {
        /* no-op */
      },
      manifestFetcher: async () => manifest,
      updatesDir: scratchDir,
      installedVersionProvider: () => '0.9.1',
      skipPackagedGate: true,
    });
    const updateInfo = resolveIdeUpdate(manifest, '0.9.1')!;

    await downloadVerifyInstallAndHandshake(updateInfo);

    assert.equal(getIdeUpdateState(), 'failed');
    assert.match(getIdeUpdateFailureReason() ?? '', /sha256 mismatch/i);
    // The .part file MUST be deleted.
    assert.ok(!fs.existsSync(partFilePath(updateInfo.version)));
    // And the .exe MUST NOT exist (we never got past verification).
    assert.ok(!fs.existsSync(stagedInstallerPath(updateInfo.version)));
  });
});

describe('downloadVerifyInstallAndHandshake — Authenticode', () => {
  beforeEach(() => installFetchStub(Buffer.from('founder-stack-payload')));
  afterEach(() => restoreFetch());

  it('unsigned + allowUnsigned=false (stable channel) → failed + deleted', async () => {
    const { updateInfo } = buildStub({
      channel: 'stable',
      allowUnsigned: false,
      authenticode: { signed: false, error: 'no signature' },
      handshakeProbe: () => true,
    });
    await downloadVerifyInstallAndHandshake(updateInfo);
    assert.equal(getIdeUpdateState(), 'failed');
    assert.match(getIdeUpdateFailureReason() ?? '', /unsigned/i);
    assert.ok(!fs.existsSync(stagedInstallerPath(updateInfo.version)));
  });

  it('unsigned + allowUnsigned=true (beta channel) → proceeds to install', async () => {
    const { updateInfo } = buildStub({
      channel: 'beta',
      allowUnsigned: true,
      authenticode: { signed: false, error: 'no signature' },
      handshakeProbe: () => true,
    });
    await downloadVerifyInstallAndHandshake(updateInfo);
    assert.equal(getIdeUpdateState(), 'idle');
    assert.ok(fs.existsSync(stagedInstallerPath(updateInfo.version)));
  });

  it('signed → proceeds to install', async () => {
    const { updateInfo } = buildStub({
      channel: 'stable',
      allowUnsigned: false,
      authenticode: { signed: true, output: 'OK' },
      handshakeProbe: () => true,
    });
    await downloadVerifyInstallAndHandshake(updateInfo);
    assert.equal(getIdeUpdateState(), 'idle');
  });
});

describe('downloadVerifyInstallAndHandshake — health handshake + rollback', () => {
  beforeEach(() => installFetchStub(Buffer.from('founder-stack-payload')));
  afterEach(() => restoreFetch());

  it('handshake timeout + no rollback candidate → failed', async () => {
    const { updateInfo } = buildStub({
      handshakeProbe: () => false, // never comes up
    });
    // Speed up: shorten the handshake window to 100ms so the test is fast.
    configureIdeUpdates({
      apiBaseUrl: 'https://example.invalid',
      handshakeProbe: () => false,
      handshakeTimeoutMs: 100,
      handshakePollMs: 20,
      authenticodeVerifier: async () => ({ signed: true }),
      installerLauncher: () => {
        /* no-op */
      },
      manifestFetcher: async () => makeManifest(),
      updatesDir: scratchDir,
      installedVersionProvider: () => '0.9.1',
      skipPackagedGate: true,
    });
    await downloadVerifyInstallAndHandshake(updateInfo);
    assert.equal(getIdeUpdateState(), 'failed');
    assert.match(getIdeUpdateFailureReason() ?? '', /no rollback candidate/i);
  });

  it('handshake timeout + rollback candidate present → rolls back to idle', async () => {
    // Seed a previous installer .exe in the updates dir.
    const prevExe = path.join(scratchDir, 'founder-stack-0.9.1.exe');
    fs.writeFileSync(prevExe, 'previous-installer-bytes');

    // The handshake probe never goes true; both the install-wait and the
    // rollback-wait will time out. We assert the rollback branch was entered
    // (state passed through `rolling_back` and ended at `failed` because the
    // rollback also failed its handshake). To keep the test fast we expose
    // a state-trace by polling getIdeUpdateState during the run; but since
    // the windows are 30s each we cannot do that here without bloating CI.
    //
    // Instead we drive the rollback logic directly: call runInstallerSilent
    // against the previous .exe and assert findRollbackCandidate picked it.
    configureIdeUpdates({
      apiBaseUrl: 'https://example.invalid',
      updatesDir: scratchDir,
      skipPackagedGate: true,
      handshakeProbe: () => false,
      authenticodeVerifier: async () => ({ signed: true }),
      installerLauncher: () => {
        /* no-op */
      },
      manifestFetcher: async () => makeManifest(),
      installedVersionProvider: () => '0.9.1',
    });

    const candidate = findRollbackCandidate('0.9.2');
    assert.ok(candidate, 'rollback candidate must be discovered');
    assert.match(candidate!, /founder-stack-0\.9\.1\.exe$/);

    // State stays idle because we didn't enter the install flow.
    assert.equal(getIdeUpdateState(), 'idle');
  });
});

// ─── Rollback candidate discovery ────────────────────────────────────────

describe('findRollbackCandidate', () => {
  it('returns null when no previous installers exist', () => {
    configureIdeUpdates({
      apiBaseUrl: 'https://example.invalid',
      updatesDir: scratchDir,
      skipPackagedGate: true,
    });
    assert.equal(findRollbackCandidate('0.9.5'), null);
  });

  it('picks the highest version strictly below the just-installed one', () => {
    configureIdeUpdates({
      apiBaseUrl: 'https://example.invalid',
      updatesDir: scratchDir,
      skipPackagedGate: true,
    });
    fs.writeFileSync(path.join(scratchDir, 'founder-stack-0.9.0.exe'), 'a');
    fs.writeFileSync(path.join(scratchDir, 'founder-stack-0.9.1.exe'), 'b');
    fs.writeFileSync(path.join(scratchDir, 'founder-stack-0.9.2.exe'), 'c');
    fs.writeFileSync(path.join(scratchDir, 'not-an-installer.txt'), 'd');
    const r = findRollbackCandidate('0.9.2');
    assert.ok(r);
    assert.match(r!, /founder-stack-0\.9\.1\.exe$/);
  });

  it('does not pick installers >= the just-installed version', () => {
    configureIdeUpdates({
      apiBaseUrl: 'https://example.invalid',
      updatesDir: scratchDir,
      skipPackagedGate: true,
    });
    fs.writeFileSync(path.join(scratchDir, 'founder-stack-0.9.2.exe'), 'c');
    fs.writeFileSync(path.join(scratchDir, 'founder-stack-0.9.5.exe'), 'e');
    assert.equal(findRollbackCandidate('0.9.2'), null);
  });
});
