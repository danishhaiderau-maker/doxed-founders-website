import { Module, OnModuleInit } from '@nestjs/common';
import { FounderDenController } from './founder-den.controller';
import { FounderDenService } from './founder-den.service';

@Module({
  controllers: [FounderDenController],
  providers: [FounderDenService],
  exports: [FounderDenService],
})
export class FounderDenModule implements OnModuleInit {
  constructor(private readonly founderDen: FounderDenService) {}

  onModuleInit() {
    this.founderDen.syncAllFounders().catch(() => {});
  }
}
