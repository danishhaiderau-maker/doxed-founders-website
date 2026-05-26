import { Module } from '@nestjs/common';
import { GeckoterminalModule } from '../geckoterminal/geckoterminal.module';
import { DexscreenerService } from './dexscreener.service';

@Module({
  imports: [GeckoterminalModule],
  providers: [DexscreenerService],
  exports: [DexscreenerService],
})
export class DexscreenerModule {}
