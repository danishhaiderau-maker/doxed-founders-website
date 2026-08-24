import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { resolve } from 'node:path';

test('participant reduction fence has independent collision keys and dormant phases', () => {
  const schema = readFileSync(resolve(process.cwd(), 'prisma/schema.prisma'), 'utf8');
  assert.match(schema, /enum ParticipantReductionPhase[\s\S]*CLAIMED[\s\S]*SUBMITTING[\s\S]*ACKNOWLEDGED[\s\S]*CONFIRMED/);
  assert.match(schema, /@@unique\(\[participantId, reductionId\]\)/);
  assert.match(schema, /@@unique\(\[participantId, sourceEventId\]\)/);
  assert.match(schema, /@@unique\(\[participantId, sourceEventSeq\]\)/);
  assert.match(schema, /ownerToken\s+String\s+@unique/);
  assert.match(schema, /requestToken\s+String\s+@unique/);
});

test('migration enforces participant reduction collision fences in PostgreSQL', () => {
  const sql = readFileSync(resolve(process.cwd(), 'prisma/migrations/20260824120000_relay_position_reduction_audit/migration.sql'), 'utf8');
  assert.match(sql, /participantId_reductionId_key/);
  assert.match(sql, /participantId_sourceEventId_key/);
  assert.match(sql, /participantId_sourceEventSeq_key/);
  assert.match(sql, /FOREIGN KEY \("participantId"\)/);
});

test('POSITION_REDUCED ingress remains HMAC-gated and returns before any executor wake', () => {
  const source = readFileSync(resolve(process.cwd(), 'apps/api/src/trading-agents/showcase-relay-events.service.ts'), 'utf8');
  const verify = source.indexOf('const verifiedSignedPayload = this.verifySignature');
  const reduction = source.indexOf("if (event === 'POSITION_REDUCED')");
  const preWake = source.indexOf('this.execution.requestExecutorPreWake?.', reduction);
  const auditReturn = source.indexOf("action: 'POSITION_REDUCTION_AUDITED'", reduction);
  assert.ok(verify >= 0 && verify < reduction);
  assert.ok(auditReturn > reduction && auditReturn < preWake);
  assert.match(source.slice(reduction, preWake), /exchange_mutation: false/);
  assert.match(source, /Number\(body\.prior_qty\)/);
  assert.match(source, /Number\(body\.reduced_qty\)/);
  assert.match(source, /Number\(body\.remaining_qty\)/);
  assert.match(source, /Number\(body\.fill_price\)/);
  assert.match(source, /body\.reduction_id\?\.trim\(\)/);
  assert.doesNotMatch(source, /Number\(body\.before_qty\)/);
});

test('subscriber adapter is real but remains explicitly disabled and unreachable', () => {
  const source = readFileSync(resolve(process.cwd(), 'apps/api/src/trading-agents/signal-subscriber-execution.service.ts'), 'utf8');
  assert.match(source, /processAuditedPositionReductionDormant/);
  assert.match(source, /SUBSCRIBER_POSITION_REDUCTION_ENABLED/);
  assert.match(source, /POSITION_REDUCTION_EXECUTION_DISABLED/);
  assert.match(source, /new PrismaReductionFenceRepository\(this\.prisma\)/);
  assert.match(source, /submitMarketClose/);
  assert.match(source, /protectiveStopPhase: true, protectiveStopQty: true/);
  const references = source.match(/processAuditedPositionReductionDormant/g) ?? [];
  assert.equal(references.length, 1);
});
