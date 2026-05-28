import { Module } from '@nestjs/common';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { FeedModule } from '../feed/feed.module';
import { PointsModule } from '../points/points.module';
import { XSocialModule } from '../x-social/x-social.module';
import { PaperTradingController } from './paper-trading.controller';
import { PaperTradingStripeService } from './paper-trading-stripe.service';
import { PaperTradingService } from './paper-trading.service';

@Module({
  imports: [DexscreenerModule, FeedModule, PointsModule, XSocialModule],
  controllers: [PaperTradingController],
  providers: [PaperTradingService, PaperTradingStripeService],
})
export class PaperTradingModule {}
