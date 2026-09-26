import assert from 'node:assert/strict';
import test from 'node:test';
import { classifyNucleusAuthorization } from './ide-nucleus-auth';

test('Nucleus accepts a Founder Node header, fos bearer, and session JWT', () => {
  assert.deepEqual(
    classifyNucleusAuthorization('FounderNode fn_1:secret-token'),
    { kind: 'founder-node', nodeId: 'fn_1', nodeToken: 'secret-token' },
  );
  assert.deepEqual(
    classifyNucleusAuthorization('Bearer fos_fn_1:secret-token'),
    { kind: 'founder-node', nodeId: 'fn_1', nodeToken: 'secret-token' },
  );
  assert.deepEqual(classifyNucleusAuthorization('Bearer eyJhbGciOi.session'), {
    kind: 'jwt',
    token: 'eyJhbGciOi.session',
  });
  assert.equal(classifyNucleusAuthorization(undefined).kind, 'none');
  assert.equal(classifyNucleusAuthorization('Basic abc').kind, 'none');
});
