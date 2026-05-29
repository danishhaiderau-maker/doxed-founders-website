import { Module, OnModuleInit } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { FounderDenController } from './founder-den.controller';
import { FounderDenService } from './founder-den.service';

@Module({
  imports: [NotificationsModule, FounderOsModule],
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
