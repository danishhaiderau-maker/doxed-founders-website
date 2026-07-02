import { Global, Module } from '@nestjs/common';
import { ProjectsModule } from '../projects/projects.module';
import { AiInvokerService } from './ai-invoker.service';
import { AiRoutingController } from './ai-routing.controller';
import { AiRoutingService } from './ai-routing.service';

@Global()
@Module({
  imports: [ProjectsModule],
  controllers: [AiRoutingController],
  providers: [AiRoutingService, AiInvokerService],
  exports: [AiRoutingService, AiInvokerService],
})
export class AiRoutingModule {}
