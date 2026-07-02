import { Module, forwardRef } from '@nestjs/common';
import { IdeBridgeController, CursorBridgeAliasController } from './ide-bridge.controller';
import { IdeBridgeService } from './ide-bridge.service';
import { DesktopBridgeModule } from '../desktop-bridge/desktop-bridge.module';
import { FounderAgentRunModule } from '../founder-agent-run/founder-agent-run.module';
import { BuilderModule } from '../builder/builder.module';
import { ConnectedWorkspaceModule } from '../connected-workspace/connected-workspace.module';

@Module({
  imports: [
    DesktopBridgeModule,
    FounderAgentRunModule,
    // forwardRef: breaks IdeBridgeModule -> BuilderModule -> FounderNodeModule -> IdeBridgeModule cycle.
    forwardRef(() => BuilderModule),
    ConnectedWorkspaceModule,
  ],
  controllers: [IdeBridgeController, CursorBridgeAliasController],
  providers: [IdeBridgeService],
  exports: [IdeBridgeService],
})
export class IdeBridgeModule {}
