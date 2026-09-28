import type { ParticipantReductionPhase } from '@prisma/client';
import { PrismaClientKnownRequestError } from '@prisma/client/runtime/library';
import { randomUUID } from 'node:crypto';
import type { PrismaService } from '../prisma/prisma.service';
import type { ParticipantReductionFence, ReductionFenceRepository, ReductionPhase, SignedReduction } from './position-reduction-fence';

function mapped(row: { id:string; participantId:string; sourceEventId:string; reductionId:string; sourceEventSeq:number; phase:ParticipantReductionPhase; beforeQty:unknown; targetQty:unknown; reduceQty:unknown; requestToken:string; exchangeOrderId:bigint|null }): ParticipantReductionFence {
  return { id:row.id, participantId:row.participantId, sourceEventId:row.sourceEventId,
    reductionId:row.reductionId, sourceEventSeq:row.sourceEventSeq,
    phase:row.phase as ReductionPhase, beforeQty:Number(row.beforeQty), targetQty:Number(row.targetQty),
    reduceQty:Number(row.reduceQty), requestToken:row.requestToken,
    exchangeOrderId:row.exchangeOrderId?.toString() ?? null };
}

export class PrismaReductionFenceRepository implements ReductionFenceRepository {
  constructor(private readonly prisma: PrismaService) {}

  async findCollision(participantId:string, source:SignedReduction) {
    const row=await this.prisma.signalCycleParticipantReduction.findFirst({ where:{ participantId, OR:[
      { reductionId:source.reductionId }, { sourceEventId:source.eventId }, { sourceEventSeq:source.eventSeq },
    ] } });
    return row ? mapped(row) : null;
  }
  async latestSequence(participantId:string) {
    return (await this.prisma.signalCycleParticipantReduction.findFirst({ where:{participantId}, orderBy:{sourceEventSeq:'desc'}, select:{sourceEventSeq:true} }))?.sourceEventSeq ?? null;
  }
  async claim(input:Omit<ParticipantReductionFence,'id'|'phase'|'exchangeOrderId'>) {
    try {
      const row=await this.prisma.signalCycleParticipantReduction.create({ data:{ ...input, ownerToken:randomUUID() } });
      return mapped(row);
    } catch (error) {
      if (!(error instanceof PrismaClientKnownRequestError) || error.code !== 'P2002') throw error;
      const collision=await this.findCollision(input.participantId, {
        eventId:input.sourceEventId,reductionId:input.reductionId,eventSeq:input.sourceEventSeq,
        priorQty:input.beforeQty,reducedQty:input.reduceQty,remainingQty:input.targetQty,
      });
      if (!collision) throw error;
      return collision;
    }
  }
  async transition(id:string, from:ReductionPhase[], to:ReductionPhase, patch?:{exchangeOrderId?:string}) {
    const now=new Date();
    const result=await this.prisma.signalCycleParticipantReduction.updateMany({
      where:{id,phase:{in:from as ParticipantReductionPhase[]}}, data:{phase:to as ParticipantReductionPhase,
        ...(patch?.exchangeOrderId ? {exchangeOrderId:BigInt(patch.exchangeOrderId)} : {}),
        ...(to==='SUBMITTING'?{submittingAt:now}:{}), ...(to==='ACKNOWLEDGED'?{acknowledgedAt:now}:{}),
        ...(to==='CONFIRMED'?{confirmedAt:now}:{}),
      },
    });
    return result.count===1;
  }
}
