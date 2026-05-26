import { Module } from '@nestjs/common';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { FeedModule } from '../feed/feed.module';
import { PaperTradingController } from './paper-trading.controller';
import { PaperTradingStripeService } from './paper-trading-stripe.service';
import { PaperTradingService } from './paper-trading.service';

@Module({
  imports: [DexscreenerModule, FeedModule],
  controllers: [PaperTradingController],
  providers: [PaperTradingService, PaperTradingStripeService],
})
export class PaperTradingModule {}
