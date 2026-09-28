import assert from 'node:assert/strict';
import test from 'node:test';
import { participantReductionPlan, processDormantPositionReduction, terminalCloseQty, type ParticipantReductionFence } from './position-reduction-fence';

const source = { eventId: 'e1', reductionId: 'r1', eventSeq: 1, priorQty: 0.02, reducedQty: 0.005, remainingQty: 0.015 };

test('maps source fraction to deterministic venue-rounded participant quantity', () => {
  assert.deepEqual(participantReductionPlan(source, 0.03127, 0.0001), { beforeQty: 0.03127, reduceQty: 0.0078, targetQty: 0.02347 });
  assert.equal(terminalCloseQty(0.02347, 0.0001), 0.0234);
});

test('restart in SUBMITTING never blind-resubmits and confirms only authenticated target', async () => {
  const fence: ParticipantReductionFence = { id:'f1', participantId:'p1', sourceEventId:'e1', reductionId:'r1', sourceEventSeq:1, phase:'SUBMITTING', beforeQty:0.04, reduceQty:0.01, targetQty:0.03, requestToken:'q1' };
  let submits=0, meta=0, protection=0;
  const repo = { findCollision:async()=>fence, latestSequence:async()=>1, claim:async()=>fence, transition:async(_id:string,_from:string[],to:string)=>{ fence.phase=to as typeof fence.phase; return true; } };
  const venue = { authenticatedPositionQty:async()=>0.03, submitReduceOnly:async()=>{submits++;return {orderId:'1'};}, replaceReduceOnlyProtection:async()=>{protection++;return true;}, updateConfirmedRemainingQty:async()=>{meta++;} };
  const result=await processDormantPositionReduction({participantId:'p1',source,venueStep:0.0001,requestToken:'q1',repo,venue});
  assert.equal(result.reason,'RECOVERED_FROM_AUTHENTICATED_TARGET'); assert.equal(submits,0); assert.equal(meta,1); assert.equal(protection,1);
});

test('duplicate conflict and out-of-order evidence fail closed', async () => {
  const conflicting: ParticipantReductionFence = { id:'f1',participantId:'p1',sourceEventId:'e1',reductionId:'r1',sourceEventSeq:1,phase:'CLAIMED',beforeQty:0.04,reduceQty:0.02,targetQty:0.02,requestToken:'q1' };
  const venue={authenticatedPositionQty:async()=>0.04,submitReduceOnly:async()=>({orderId:'1'}),replaceReduceOnlyProtection:async()=>true,updateConfirmedRemainingQty:async()=>{}};
  const base={latestSequence:async()=>2,claim:async()=>conflicting,transition:async()=>true};
  await assert.rejects(processDormantPositionReduction({participantId:'p1',source,venueStep:0.0001,requestToken:'q1',repo:{...base,findCollision:async()=>conflicting},venue}),/REDUCTION_FENCE_CONFLICT/);
  await assert.rejects(processDormantPositionReduction({participantId:'p1',source,venueStep:0.0001,requestToken:'q1',repo:{...base,findCollision:async()=>null},venue}),/REDUCTION_OUT_OF_ORDER/);
});
