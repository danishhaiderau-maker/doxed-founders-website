import { Module } from '@nestjs/common';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { MemoryEngineService } from './memory-engine.service';
import { MemoryEngineController } from './memory-engine.controller';

@Module({
  // FounderNodeGuard (used by MemoryEngineController) injects FounderNodeService,
  // which is provided by FounderNodeModule. Import it so the guard can resolve
  // its dependency at bootstrap. Without this, NestJS crashes with
  // "Nest can't resolve dependencies of the FounderNodeGuard (?)".
  imports: [FounderNodeModule],
  providers: [MemoryEngineService],
  controllers: [MemoryEngineController],
  exports: [MemoryEngineService],
})
export class MemoryEngineModule {}
