import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { PointsModule } from '../points/points.module';
import { ExchangesModule } from '../exchanges/exchanges.module';
import { AgentRegistryController } from './agent-registry.controller';
import { AgentRegistryService } from './agent-registry.service';
import { BotBridgeService } from './bot-bridge.service';
import { SignalApiKeyGuard } from './signal-api-key.guard';
import { SignalCyclesController } from './signal-cycles.controller';
import { SignalCyclesService } from './signal-cycles.service';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';
import { CopyRelaySimService } from './copy-relay-sim.service';
import { TradeCycleAuditService } from './trade-cycle-audit.service';
import { TradingAgentInstancesService } from './trading-agent-instances.service';
import { TradingAgentsController } from './trading-agents.controller';
import { TradingAgentsService } from './trading-agents.service';

@Module({
  imports: [NotificationsModule, PointsModule, ExchangesModule],
  controllers: [TradingAgentsController, SignalCyclesController, AgentRegistryController],
  providers: [
    TradingAgentsService,
    TradingAgentInstancesService,
    BotBridgeService,
    SignalCyclesService,
    SignalSubscriberExecutionService,
    CopyRelaySimService,
    TradeCycleAuditService,
    SignalApiKeyGuard,
    AgentRegistryService,
  ],
  exports: [TradingAgentsService, BotBridgeService, TradingAgentInstancesService, SignalCyclesService, AgentRegistryService, CopyRelaySimService],
})
export class TradingAgentsModule {}
