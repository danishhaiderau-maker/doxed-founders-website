import { Module, forwardRef } from '@nestjs/common';
import { AiProxyModule } from '../ai-proxy/ai-proxy.module';
import { FlightRecorderModule } from '../flight-recorder/flight-recorder.module';
import { ExecutionManagerModule } from '../execution-manager/execution-manager.module';
import { IntentEngineController } from './intent-engine.controller';
import { IntentEngineService } from './intent-engine.service';

@Module({
  imports: [
    forwardRef(() => AiProxyModule),
    FlightRecorderModule,
    forwardRef(() => ExecutionManagerModule),
  ],
  controllers: [IntentEngineController],
  providers: [IntentEngineService],
  exports: [IntentEngineService],
})
export class IntentEngineModule {}
