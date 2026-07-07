import { Module } from '@nestjs/common';
import { MemoryEngineService } from './memory-engine.service';

@Module({
  providers: [MemoryEngineService],
  exports: [MemoryEngineService],
})
export class MemoryEngineModule {}
