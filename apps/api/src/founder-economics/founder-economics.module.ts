import { Module } from '@nestjs/common';
import { DdollarModule } from '../ddollar/ddollar.module';
import { GitHubModule } from '../github/github.module';
import { DdollarGateTokenGuard } from './ddollar-gate-token.guard';
import { DdollarEngineService } from './ddollar-engine.service';
import { EpochSettlementService } from './epoch-settlement.service';
import { FounderEconomicsController } from './founder-economics.controller';
import { FounderGdpService } from './founder-gdp.service';
import { KnowledgeGraphService } from './knowledge-graph.service';
import { ProofOfSuccessService } from './proof-of-success.service';

@Module({
  imports: [DdollarModule, GitHubModule],
  controllers: [FounderEconomicsController],
  providers: [
    DdollarEngineService,
    DdollarGateTokenGuard,
    KnowledgeGraphService,
    ProofOfSuccessService,
    EpochSettlementService,
    FounderGdpService,
  ],
  exports: [
    DdollarEngineService,
    KnowledgeGraphService,
    ProofOfSuccessService,
    EpochSettlementService,
    FounderGdpService,
  ],
})
export class FounderEconomicsModule {}

