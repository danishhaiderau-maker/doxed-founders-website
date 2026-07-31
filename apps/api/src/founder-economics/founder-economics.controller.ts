import {
  Body,
  Controller,
  Get,
  Logger,
  Param,
  Post,
  Query,
} from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import {
  DDOLLAR_ACTIVITY_SPECS,
  MILESTONE_TIERS,
  type KnowledgeType,
  type ProofType,
} from '@dcf/utils';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { DdollarEngineService } from './ddollar-engine.service';
import { EpochSettlementService } from './epoch-settlement.service';
import { FounderGdpService } from './founder-gdp.service';
import { KnowledgeGraphService } from './knowledge-graph.service';
import { ProofOfSuccessService } from './proof-of-success.service';

@SkipThrottle()
@Controller('founder-economics')
export class FounderEconomicsController {
  private readonly logger = new Logger(FounderEconomicsController.name);

  constructor(
    private readonly gdpService: FounderGdpService,
    private readonly ddollarEngine: DdollarEngineService,
    private readonly knowledgeGraph: KnowledgeGraphService,
    private readonly proof: ProofOfSuccessService,
    private readonly settlement: EpochSettlementService,
  ) {}

  /** Public — Founder GDP dashboard metrics. */
  @Public()
  @Get('gdp')
  gdp() {
    return this.gdpService.computeGdp();
  }

  /** Public — epoch history. */
  @Public()
  @Get('epochs')
  epochs(@Query('limit') limit?: string) {
    return this.settlement.epochHistory(limit ? Number(limit) : 25);
  }

  /** Public — DDollar scoring table (what earns DDollar). */
  @Public()
  @Get('ddollar/scoring')
  ddollarScoring() {
    return {
      activities: DDOLLAR_ACTIVITY_SPECS,
      milestoneTiers: MILESTONE_TIERS,
    };
  }

  /** Authenticated — DDollar balance for the signed-in founder. */
  @Get('ddollar/balance')
  ddollarBalance(@CurrentUser() user: AuthUser) {
    return this.ddollarEngine.exportSnapshot(0).then((snap) => {
      const founder = snap.founders.find((f) => f.userId === user.id);
      return {
        userId: user.id,
        rawDdollar: founder?.rawDdollar ?? user.reputationPoints,
        reputationMultiplierInputs: founder?.reputationMultiplierInputs ?? null,
      };
    });
  }

  /**
   * Authenticated — DDollar gate shim. Translates the canonical rawDdollar
   * ledger (User.reputationPoints) into the exact JSON shape the strategy
   * bot's DDOLLAR_GATE_URL expects: `{ "ddollarBalance": <number> }`.
   *
   * The bot reads one of `ddollar | balance | ddollarBalance | points` as a
   * numeric field; we emit `ddollarBalance` so the field is unambiguous and
   * stable. Auth is the standard @CurrentUser session — no separate token,
   * no bypass. The value is the same rawDdollar source the founder-economics
   * snapshot exposes, rounded to 2 decimals for display stability.
   */
  @Get('ddollar/gate-balance')
  ddollarGateBalance(@CurrentUser() user: AuthUser) {
    return this.ddollarEngine.exportSnapshot(0).then((snap) => {
      const founder = snap.founders.find((f) => f.userId === user.id);
      const rawDdollar = founder?.rawDdollar ?? user.reputationPoints ?? 0;
      const ddollarBalance = Number(
        (typeof rawDdollar === 'number' && Number.isFinite(rawDdollar)
          ? rawDdollar
          : 0
        ).toFixed(2),
      );
      return { ddollarBalance };
    });
  }

  /** Public — DDollar balance by userId (used by profile pages). */
  @Public()
  @Get('ddollar/balance/:userId')
  ddollarBalanceFor(@Param('userId') userId: string) {
    return this.ddollarEngine.exportSnapshot(0).then((snap) => {
      const founder = snap.founders.find((f) => f.userId === userId);
      return {
        userId,
        rawDdollar: founder?.rawDdollar ?? 0,
        reputationMultiplierInputs: founder?.reputationMultiplierInputs ?? null,
      };
    });
  }

  /** Authenticated — claimable tokens for the signed-in founder. */
  @Get('claimable')
  claimable(@CurrentUser() user: AuthUser) {
    return this.settlement.claimableForFounder(user.id);
  }

  /**
   * Authenticated — submit a claim for a specific epoch. Returns the Merkle
   * proof the founder's wallet needs to call EpochDistributor.claim() on-chain.
   * The on-chain call itself is made by the founder's wallet; this endpoint
   * just hands over the proof.
   */
  @Post('claim')
  claim(
    @CurrentUser() user: AuthUser,
    @Body() body: { epochId: string; walletAddress: string },
  ) {
    return this.settlement.claimableForFounder(user.id).then((rows) => {
      const row = rows.find((r) => r.epochId === body.epochId);
      if (!row) {
        return { found: false, message: 'No claimable allocation for this epoch.' };
      }
      return {
        found: true,
        epochId: body.epochId,
        epochNumber: row.epochNumber,
        amount: row.amount,
        walletAddress: row.walletAddress,
        merkleRoot: row.merkleRoot,
        merkleProof: row.merkleProof,
        // Founder's wallet calls EpochDistributor.claim(epoch, account, amount, proof)
        // with these values.
      };
    });
  }

  /** Public — recent knowledge graph for visualization. */
  @Public()
  @Get('knowledge')
  knowledge(@Query('limit') limit?: string) {
    return this.knowledgeGraph.recentKnowledge(limit ? Number(limit) : 25);
  }

  /** Authenticated — contribute a knowledge node. */
  @Post('knowledge/contribute')
  contributeKnowledge(
    @CurrentUser() user: AuthUser,
    @Body() body: { knowledgeType: KnowledgeType; content: string; parentNodeId?: string },
  ) {
    return this.knowledgeGraph.contribute(
      user.id,
      body.knowledgeType,
      body.content,
      body.parentNodeId,
    );
  }

  /** Public — verified milestones for a founder. */
  @Public()
  @Get('proofs/:userId')
  founderProofs(@Param('userId') userId: string) {
    return this.proof.founderProofs(userId);
  }

  /** Public — milestone tier table. */
  @Public()
  @Get('proofs/tiers')
  proofTiers() {
    return this.proof.milestoneTiersTable();
  }

  /** Authenticated — submit a Proof of Success for verification. */
  @Post('proofs/verify')
  verifyProof(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      proofType: ProofType;
      externalId: string;
      /** Optional — when omitted, live fetch from GitHub/Vercel/Stripe is attempted. */
      verifiedMetric?: number;
      rawPayload?: unknown;
    },
  ) {
    return this.proof.verify(
      user.id,
      body.proofType,
      body.externalId,
      body.verifiedMetric,
      body.rawPayload,
    );
  }

  /** Authenticated — manually trigger epoch settlement (admin/dev only). */
  @Post('settle')
  settle() {
    return this.settlement.settleCurrentEpoch();
  }
}
