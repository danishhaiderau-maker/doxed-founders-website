import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { APPLY_FLAG, REVIEWED_MIGRATIONS, checksumVariants, ledgerFingerprint, loadReviewedMigrations, parseMode, runReviewedRelaySchema, splitPinnedSqlStatements, validatePreflight, validatePostSchema } from './apply-reviewed-relay-schema-20260920.mjs';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const hash = text => createHash('sha256').update(text).digest('hex');
function fixture() {
  const rows = Array.from({ length:25 }, (_,i) => ({
    id:'id-'+i, migration_name:i===0?'20260706120000_phase15_trust_layer':i===1?'20260711220000_founder_economics_mvp':'m-'+i,
    checksum:hash('source-'+i), finished_at:new Date(1000+i), rolled_back_at:null,
    started_at:new Date(i), logs:null, applied_steps_count:1,
  }));
  rows.push({...rows[0], id:'rolled', finished_at:null, rolled_back_at:new Date('2026-07-07T03:25:03.197Z'), logs:'historical failure'});
  const migrations = {
    sourceNames:rows.slice(0,25).map(r=>r.migration_name).concat(REVIEWED_MIGRATIONS.map(r=>r[0])).sort(),
    sourceChecksums:new Map(rows.map(r=>[r.migration_name,new Set([r.checksum])])),
    reviewed:REVIEWED_MIGRATIONS.map(([name,sha256])=>({name,sha256,sql:'SQL '+name})),
  };
  REVIEWED_MIGRATIONS.forEach(([n,h])=>migrations.sourceChecksums.set(n,new Set([h])));
  return { migrations, state:{ledgerRows:rows, enabledDdlTriggers:[], activeDdl:[], presentTables:[],enumPresent:false,presentColumns:[],sourceIndexPresent:false,participantIdType:'text',renamedIndexes:[],oldIndexes:[]} };
}
const post = state => ({...state, presentTables:['RelayPositionReductionAudit','SignalCycleParticipantReduction'],enumPresent:true,presentColumns:['platformReceivedAt','sourceEventId','sourceEventSeq','sourcePayloadSha256'],sourceIndexPresent:true,renamedIndexes:['SignalCycleParticipantReduction_participantId_sourceEventSe_key','SignalCycleParticipantReduction_participantId_phase_created_idx']});

test('all three exact SQL files pinned; historical SQL unchanged',async()=>{
  const m=await loadReviewedMigrations(root);
  assert.deepEqual(m.reviewed.map(({name,sha256})=>[name,sha256]),REVIEWED_MIGRATIONS);
  assert.deepEqual(m.reviewed.map(({sql})=>splitPinnedSqlStatements(sql).length),[15,2,2]);
  assert.match(m.reviewed[2].sql,/ALTER INDEX "public"/);
});
test('LF and CRLF only are approved checksum representations',()=>{
  const variants=checksumVariants(Buffer.from('one\r\ntwo\r\n'));
  assert(variants.has(hash('one\ntwo\n')));
  assert(variants.has(hash('one\r\ntwo\r\n')));
  assert(!variants.has(hash('one\ntwo changed\n')));
  assert(!variants.has(hash('\ufeffone\ntwo\n')));
});
test('dry-run defaults; exact apply flag required',()=>{
  assert.equal(parseMode([]),'dry-run'); assert.equal(parseMode(['--dry-run']),'dry-run');
  assert.equal(parseMode([APPLY_FLAG]),'apply'); assert.throws(()=>parseMode(['--apply']));
});
test('only precise reviewed rollback plus 25 unique applied accepted',()=>{
  const {state,migrations}=fixture();
  assert.equal(validatePreflight(state,migrations).applied,25);
  for(const change of [
    s=>s.ledgerRows.pop(),
    s=>s.ledgerRows[25].rolled_back_at=new Date(),
    s=>s.ledgerRows[25].migration_name='other',
    s=>s.ledgerRows[2].finished_at=null,
    s=>s.ledgerRows[3].migration_name=s.ledgerRows[2].migration_name,
    s=>s.ledgerRows[25].checksum='unknown',
    s=>s.ledgerRows[2].checksum='unknown',
  ]) {
    const altered=structuredClone(state); change(altered);
    assert.throws(()=>validatePreflight(altered,migrations));
  }
});
test('only the named economics mismatch is permitted and fingerprinted',()=>{
 const {state,migrations}=fixture(); state.ledgerRows[1].checksum='reviewed-economic-anomaly';
 assert.deepEqual(validatePreflight(state,migrations).historicalMismatch,['20260711220000_founder_economics_mvp']);
 const before=ledgerFingerprint(state.ledgerRows); state.ledgerRows[1].checksum='another';
 assert.notEqual(ledgerFingerprint(state.ledgerRows),before);
});
test('fingerprint covers rollback, logs, startedAt, count and every old row field',()=>{
 const {state}=fixture(); const before=ledgerFingerprint(state.ledgerRows);
 for(const field of ['rolled_back_at','logs','started_at','applied_steps_count']){
   const rows=structuredClone(state.ledgerRows); rows[25][field]='changed';
   assert.notEqual(ledgerFingerprint(rows),before);
 }
 assert.equal(ledgerFingerprint([...state.ledgerRows].reverse()),before);
});
test('dry-run never enters transaction',async()=>{
 const {state,migrations}=fixture();
 await runReviewedRelaySchema({migrations,mode:'dry-run',adapter:{inspect:async()=>state,transaction:()=>assert.fail()}});
});
test('apply requires pinned ledger and executes exactly three payloads in one bounded transaction',async()=>{
 const {state,migrations}=fixture(); const calls=[]; let count=0;
 const adapter={inspect:async()=>state,transaction:async(fn,limits)=>{
   calls.push(limits);
   await fn({setTimeouts:async()=>calls.push('timeouts'),inspect:async()=>count===3?post(state):state,executePinnedSql:async sql=>{calls.push(sql);count++;}});
 }};
 await assert.rejects(runReviewedRelaySchema({adapter,migrations,mode:'apply'}),/EXPECTED_LEDGER/);
 await runReviewedRelaySchema({adapter,migrations,mode:'apply',expectedLedgerFingerprint:ledgerFingerprint(state.ledgerRows)});
 assert.deepEqual(calls,[{maxWaitMs:3000,timeoutMs:45000},'timeouts',...migrations.reviewed.map(r=>r.sql)]);
});
test('changed ledger before transaction rejects without executing',async()=>{
 const {state,migrations}=fixture(); const inside=structuredClone(state);inside.ledgerRows[25].logs='changed';
 const adapter={inspect:async()=>state,transaction:async fn=>fn({setTimeouts:async()=>{},inspect:async()=>inside,executePinnedSql:()=>assert.fail()})};
 await assert.rejects(runReviewedRelaySchema({adapter,migrations,mode:'apply',expectedLedgerFingerprint:ledgerFingerprint(state.ledgerRows)}),/changed before/);
});
test('postflight requires renames and preserves historical ledger',()=>{
 const {state}=fixture();const expected=ledgerFingerprint(state.ledgerRows);
 validatePostSchema(post(state),expected);
 assert.throws(()=>validatePostSchema({...post(state),renamedIndexes:[]},expected),/renames/);
 assert.throws(()=>validatePostSchema({...post(state),oldIndexes:['old']},expected),/renames/);
 const changed=post(structuredClone(state));changed.ledgerRows[25].logs='changed';
 assert.throws(()=>validatePostSchema(changed,expected),/ledger changed/);
});
test('schema partial state, active DDL or event triggers fail closed',()=>{
 for(const change of [
   {presentTables:['RelayPositionReductionAudit']},{enumPresent:true},{presentColumns:['sourceEventId']},
   {sourceIndexPresent:true},{enabledDdlTriggers:[{}]},{activeDdl:[{}]},{participantIdType:'uuid'},
 ]){const {state,migrations}=fixture();assert.throws(()=>validatePreflight({...state,...change},migrations));}
});
