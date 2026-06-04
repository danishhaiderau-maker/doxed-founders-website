import { Module } from '@nestjs/common';
import { AirdropController } from './airdrop.controller';
import { BuilderRewardsController } from './builder-rewards.controller';
import { AirdropService } from './airdrop.service';

@Module({
  controllers: [AirdropController, BuilderRewardsController],
  providers: [AirdropService],
  exports: [AirdropService],
})
export class AirdropModule {}
