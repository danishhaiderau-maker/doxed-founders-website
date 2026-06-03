import { Module } from '@nestjs/common';
import { GitHubModule } from '../github/github.module';
import { AdminControlController } from './admin-control.controller';
import { AdminControlService } from './admin-control.service';
import { ShowcaseRuntimeService } from './showcase-runtime.service';
import { TradingAgentsModule } from '../trading-agents/trading-agents.module';

@Module({
  imports: [TradingAgentsModule, GitHubModule],
  controllers: [AdminControlController],
  providers: [AdminControlService, ShowcaseRuntimeService],
  exports: [AdminControlService],
})
export class AdminControlModule {}
