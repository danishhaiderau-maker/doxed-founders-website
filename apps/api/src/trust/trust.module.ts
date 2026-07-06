import { Module } from '@nestjs/common';
import { TrustCenterModule } from '../trust-center/trust-center.module';
import { AntiSybilService } from './anti-sybil.service';

@Module({
  imports: [TrustCenterModule],
  providers: [AntiSybilService],
  exports: [AntiSybilService],
})
export class TrustModule {}
