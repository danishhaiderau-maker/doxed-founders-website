import { Module } from '@nestjs/common';
import { FounderAgentRunService } from './founder-agent-run.service';

@Module({
  providers: [FounderAgentRunService],
  exports: [FounderAgentRunService],
})
export class FounderAgentRunModule {}
