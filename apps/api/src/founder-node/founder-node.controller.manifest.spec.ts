/**
 * Unit tests for the public GET /api/founder-node/manifest endpoint.
 *
 * The endpoint serves the canonical Founder IDE update manifest from
 * packages/founder-ide/updates/founder-stack-updates.json. We exercise the
 * controller method directly (no Nest bootstrap) with stubbed collaborators
 * — the manifest read path is real, so the assertions confirm the file
 * resolves correctly from this repo layout too.
 */

import { describe, it, before, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {
  FounderNodeController,
  __resetFounderManifestCacheForTests,
} from './founder-node.controller';

type ExpressResponseStub = {
  headers: Record<string, string>;
  setHeader(name: string, value: string): void;
};

function makeRes(): ExpressResponseStub {
  const headers: Record<string, string> = {};
  return {
    headers,
    setHeader(name: string, value: string) {
      headers[name.toLowerCase()] = value;
    },
  };
}

// Minimal stub of the five controller dependencies. Manifest serving
// touches none of them, so they can be no-ops with loose typing.
function makeController() {
  return new FounderNodeController(
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );
}

describe('FounderNodeController — GET /manifest', () => {
  before(() => {
    // Ensure we resolve the committed manifest regardless of where the
    // test runner sets cwd. The controller resolves relative to cwd by
    // default, but tsx --test may run from anywhere; pinning via env makes
    // the test deterministic.
    const repoRoot = path.resolve(__dirname, '..', '..', '..', '..');
    process.env.FOUNDER_IDE_MANIFEST_PATH = path.join(
      repoRoot,
      'packages',
      'founder-ide',
      'updates',
      'founder-stack-updates.json',
    );
  });

  afterEach(() => {
    __resetFounderManifestCacheForTests();
  });

  it('returns the manifest body (object with a releases array)', () => {
    const controller = makeController();
    const res = makeRes();
    const body = controller.getManifest(res as never);

    assert.equal(typeof body, 'object');
    assert.ok(body, 'expected manifest body to be truthy');
    const manifest = body as { releases?: unknown[] };
    assert.ok(Array.isArray(manifest.releases), 'expected releases to be an array');
    assert.ok((manifest.releases as unknown[]).length > 0, 'expected at least one release entry');
  });

  it('the 0.9.0 release entry has status "current"', () => {
    const controller = makeController();
    const res = makeRes();
    const body = controller.getManifest(res as never) as {
      releases: Array<{ version: string; status: string }>;
    };
    const entry = body.releases.find((r) => r.version === '0.9.0');
    assert.ok(entry, 'expected a 0.9.0 entry in releases');
    assert.equal(entry!.status, 'current');
  });

  it('sets Content-Type to application/json', () => {
    const controller = makeController();
    const res = makeRes();
    controller.getManifest(res as never);
    const ct = res.headers['content-type'];
    assert.ok(ct, 'expected Content-Type header to be set');
    assert.ok(
      ct!.toLowerCase().startsWith('application/json'),
      `expected application/json, got ${ct}`,
    );
  });

  it('sets Cache-Control: public, max-age=60', () => {
    const controller = makeController();
    const res = makeRes();
    controller.getManifest(res as never);
    const cc = res.headers['cache-control'];
    assert.ok(cc, 'expected Cache-Control header to be set');
    assert.match(cc!, /public/i);
    assert.match(cc!, /max-age=60\b/);
  });
});
