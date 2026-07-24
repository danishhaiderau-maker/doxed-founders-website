import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import {
  FounderVerifiedSolutionMemory,
  isVerificationCommand,
} from './verified-solution-memory';

const hash = (digit: string) => digit.repeat(64);

describe('Founder verified solution memory', () => {
  it('remembers only edited work with a successful engineering check', () => {
    const store = new FounderVerifiedSolutionMemory(
      mkdtempSync(join(tmpdir(), 'founder-solutions-')),
    );
    assert.equal(store.remember({
      workspaceId: 'workspace',
      goal: 'Fix the gateway streaming parser',
      summary: 'Preserved streamed tool calls and the completion marker.',
      affectedFiles: [{ path: 'src/gateway.ts', sha256: hash('a') }],
      checks: [{ command: 'npm test', result: 'passed' }],
      commit: 'abc123',
    }), true);
    assert.equal(store.list('workspace').length, 1);
    assert.equal(store.remember({
      workspaceId: 'workspace',
      goal: 'Unverified edit',
      summary: 'Changed a file.',
      affectedFiles: [{ path: 'src/other.ts', sha256: hash('b') }],
      checks: [{ command: 'Get-ChildItem', result: 'passed' }],
    }), false);
  });

  it('surfaces a relevant pattern only while affected hashes match', () => {
    const store = new FounderVerifiedSolutionMemory(
      mkdtempSync(join(tmpdir(), 'founder-solutions-')),
    );
    store.remember({
      workspaceId: 'workspace',
      goal: 'Fix gateway streaming parser',
      summary: 'Keep tool deltas until the terminal completion marker.',
      affectedFiles: [{ path: 'src/gateway.ts', sha256: hash('a') }],
      checks: [{ command: 'npm run test:gateway', result: 'passed' }],
      commit: 'abc123',
    });
    const current = [{ path: 'src/gateway.ts', sha256: hash('a') }];
    assert.match(
      store.contextFor('workspace', 'gateway streaming is broken', current),
      /Verified prior solution patterns/,
    );
    assert.equal(
      store.contextFor(
        'workspace',
        'gateway streaming is broken',
        [{ path: 'src/gateway.ts', sha256: hash('b') }],
      ),
      '',
    );
    assert.equal(
      store.contextFor('workspace', 'change the sidebar color', current),
      '',
    );
  });
});

it('recognizes tests and builds but not arbitrary successful commands', () => {
  assert.equal(isVerificationCommand('npm run typecheck'), true);
  assert.equal(isVerificationCommand('node --test src/example.spec.ts'), true);
  assert.equal(isVerificationCommand('Get-ChildItem'), false);
});
