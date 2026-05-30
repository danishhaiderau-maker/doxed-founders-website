import { Module } from '@nestjs/common';
import { ReputationModule } from '../reputation/reputation.module';
import { AccountController } from './account.controller';
import { AccountService } from './account.service';

@Module({
  imports: [ReputationModule],
  controllers: [AccountController],
  providers: [AccountService],
  exports: [AccountService],
})
export class AccountModule {}
