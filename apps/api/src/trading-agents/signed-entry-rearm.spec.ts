import assert from 'node:assert/strict';
import test from 'node:test';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';

for (const offset of [-1, 0, 1]) test(`actual signed entry rechecks fresh arm: cycle delta ${offset}`, async () => {
  const previous = {kill:process.env.INTENT_MIRROR_KILL_SWITCH,dry:process.env.INTENT_MIRROR_DRY_RUN};
  process.env.INTENT_MIRROR_KILL_SWITCH='0';process.env.INTENT_MIRROR_DRY_RUN='0';
  try {
    const now=Date.now(), oldArm=now-2000, freshArm=now-1000;
    const state=(arm:number)=>({relayExecutionMode:'LIVE',relayPolicyVersion:'two_lane_explicit_v6',realTradingConfirmedAt:new Date(arm).toISOString(),relayArmedAt:new Date(arm).toISOString()});
    const instance={id:'instance',userId:'user',status:'ACTIVE',exchangeProvider:'BITFINEX',dashboardState:state(oldArm)};
    const cycle={id:'cycle',tradeId:'cont-aabbccddeeff',createdAt:new Date(freshArm+offset),intentEnvelope:{schema:'dcf-signal-intent/v1',signalId:'cont-aabbccddeeff',trade_id:'cont-aabbccddeeff',action:'ENTER',direction:'LONG',entry:{mode:'EXACT_LIMIT',reference:'SHOWCASE_EXACT_LIMIT',exact_limit_price:64000,exact_qty_btc:0.001},context:{signed_showcase_event:true,showcase_event:'ORDER_PLACED',platform_received_at:new Date(now).toISOString(),entry_limit_policy:'micro_sr_structural_limit_v1'}}};
    let placements=0;const reasons:string[]=[];
    const service=Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.logger={warn:(s:string)=>reasons.push(s),log:()=>{}};
    service.prisma={signalCycle:{findMany:async(query:any)=>{assert.equal(query.where.createdAt.gt.getTime(),oldArm);return[cycle];}},platformSettings:{findUnique:async()=>null},signalCycleParticipant:{findMany:async()=>[]},tradingAgentInstance:{findUnique:async()=>({...instance,dashboardState:state(freshArm)})}};
    service.exchanges={getUserCredentials:async()=>({})};
    service.activeTrading={listActiveOrders:async()=>[],getOpenPositionDetail:async()=>null,getDerivativesAvailableUsd:async()=>100,getMarkPrice:async()=>64000};
    service.botBridge={getCachedExecutionState:()=>null};
    service.evaluateEntryEligibility=async()=>({canEnter:true});
    service.placeEntry=async()=>{placements++;return true;};
    const result=await service.tryFreshSignedFlatEntry('agent',instance);
    assert.equal(result,offset>0,reasons.join(';'));assert.equal(placements,offset>0?1:0);
    if(offset<=0)assert(reasons.some(s=>s.includes('cycle-not-fresh')),reasons.join(';'));
  } finally {
    for(const [key,value] of [['INTENT_MIRROR_KILL_SWITCH',previous.kill],['INTENT_MIRROR_DRY_RUN',previous.dry]]) {if(value==null)delete process.env[key!];else process.env[key!]=value;}
  }
});
