import { Module } from '@nestjs/common';
import { ConnectedWorkspaceController } from './connected-workspace.controller';
import { ConnectedWorkspaceService } from './connected-workspace.service';

@Module({
  controllers: [ConnectedWorkspaceController],
  providers: [ConnectedWorkspaceService],
  exports: [ConnectedWorkspaceService],
})
export class ConnectedWorkspaceModule {}
