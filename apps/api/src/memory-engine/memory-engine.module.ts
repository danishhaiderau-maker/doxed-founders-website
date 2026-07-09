import { Module } from '@nestjs/common';
import { MemoryEngineService } from './memory-engine.service';
import { MemoryEngineController } from './memory-engine.controller';

@Module({
  providers: [MemoryEngineService],
  controllers: [MemoryEngineController],
  exports: [MemoryEngineService],
})
export class MemoryEngineModule {}
