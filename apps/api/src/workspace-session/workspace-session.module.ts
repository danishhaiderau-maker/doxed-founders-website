import { Module } from '@nestjs/common';
import { WorkspaceSessionController } from './workspace-session.controller';
import { WorkspaceSessionService } from './workspace-session.service';

@Module({
  controllers: [WorkspaceSessionController],
  providers: [WorkspaceSessionService],
  exports: [WorkspaceSessionService],
})
export class WorkspaceSessionModule {}
