import assert from 'node:assert/strict';
import test from 'node:test';
import {
  BACKEND_API_GONE_CODE,
  BackendApiRetiredError,
  backendApiGonePayload,
  isApiDependentPath,
  isBackendApiRetired,
  throwIfBackendApiRetired,
} from './backend-api-retired';

test('isBackendApiRetired defaults ON (Railway/Neon removal / t781u)', () => {
  const prev = process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
  delete process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
  try {
    assert.equal(isBackendApiRetired(), true);
  } finally {
    if (prev === undefined) delete process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
    else process.env.NEXT_PUBLIC_BACKEND_API_RETIRED = prev;
  }
});

test('isBackendApiRetired can be opted out for lab/unit stubs', () => {
  const prev = process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
  process.env.NEXT_PUBLIC_BACKEND_API_RETIRED = '0';
  try {
    assert.equal(isBackendApiRetired(), false);
  } finally {
    if (prev === undefined) delete process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
    else process.env.NEXT_PUBLIC_BACKEND_API_RETIRED = prev;
  }
});

test('throwIfBackendApiRetired raises typed error with t781u cite payload', () => {
  const prev = process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
  delete process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
  try {
    assert.throws(() => throwIfBackendApiRetired(), (err: unknown) => {
      assert.ok(err instanceof BackendApiRetiredError);
      assert.equal(err.code, BACKEND_API_GONE_CODE);
      assert.match(err.message, /t781u|Railway|Neon|retired/i);
      return true;
    });
    const payload = backendApiGonePayload();
    assert.equal(payload.code, BACKEND_API_GONE_CODE);
    assert.equal(payload.cite, 't781u');
    assert.equal(payload.fly, 'deleted');
    assert.equal(payload.r50c, 'static-site-reduces-api-dependency');
  } finally {
    if (prev === undefined) delete process.env.NEXT_PUBLIC_BACKEND_API_RETIRED;
    else process.env.NEXT_PUBLIC_BACKEND_API_RETIRED = prev;
  }
});

test('isApiDependentPath covers login, x-sync account, x402, trading', () => {
  assert.equal(isApiDependentPath('/login'), true);
  assert.equal(isApiDependentPath('/account?tab=connected'), true);
  assert.equal(isApiDependentPath('/docs/signal-api'), true);
  assert.equal(isApiDependentPath('/paper-trading'), true);
  assert.equal(isApiDependentPath('/agent-hub/conservative-btc'), true);
  assert.equal(isApiDependentPath('/'), false);
  assert.equal(isApiDependentPath('/privacy'), false);
  assert.equal(isApiDependentPath('/platform-offline'), false);
});
