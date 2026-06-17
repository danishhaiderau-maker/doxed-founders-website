import { Module } from '@nestjs/common';
import { ReputationModule } from '../reputation/reputation.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { AccountController } from './account.controller';
import { AccountService } from './account.service';
import { PlatformHandleService } from './platform-handle.service';

@Module({
  imports: [ReputationModule, FounderOsModule],
  controllers: [AccountController],
  providers: [AccountService, PlatformHandleService],
  exports: [AccountService, PlatformHandleService],
})
export class AccountModule {}
