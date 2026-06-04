import { Module } from '@nestjs/common';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { FeedModule } from '../feed/feed.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { PointsModule } from '../points/points.module';
import { XSocialModule } from '../x-social/x-social.module';
import { PaperTradingController } from './paper-trading.controller';
import { PaperTradingPaymentsController } from './paper-trading-payments.controller';
import { PaperLimitOrderService } from './paper-limit-order.service';
import { PaperTradingCryptoService } from './paper-trading-crypto.service';
import { PaperTradingStripeService } from './paper-trading-stripe.service';
import { PaperTradingService } from './paper-trading.service';
import { TraderVerificationService } from './trader-verification.service';

@Module({
  imports: [DexscreenerModule, FeedModule, PointsModule, XSocialModule, NotificationsModule],
  controllers: [PaperTradingController, PaperTradingPaymentsController],
  providers: [
    PaperTradingService,
    PaperLimitOrderService,
    PaperTradingStripeService,
    PaperTradingCryptoService,
    TraderVerificationService,
  ],
  exports: [TraderVerificationService],
})
export class PaperTradingModule {}
