import { Module, forwardRef } from '@nestjs/common';
import { ReputationModule } from '../reputation/reputation.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { AccountController } from './account.controller';
import { AccountService } from './account.service';
import { PlatformHandleService } from './platform-handle.service';
import { ReferralService } from './referral.service';

@Module({
  imports: [ReputationModule, forwardRef(() => FounderOsModule)],
  controllers: [AccountController],
  providers: [AccountService, PlatformHandleService, ReferralService],
  exports: [AccountService, PlatformHandleService, ReferralService],
})
export class AccountModule {}
