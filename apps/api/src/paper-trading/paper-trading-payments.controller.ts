import { Body, Controller, Post } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { TopUpPaymentAsset } from '@prisma/client';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { ConfirmCryptoTopUpDto, CreateCryptoTopUpDto } from './dto/paper-trading.dto';
import { PaperTradingCryptoService } from './paper-trading-crypto.service';

@SkipThrottle()
@Controller('paper-trading')
export class PaperTradingPaymentsController {
  constructor(private readonly crypto: PaperTradingCryptoService) {}

  @Post('crypto/intent')
  createCryptoIntent(
    @CurrentUser() user: AuthUser,
    @Body() dto: CreateCryptoTopUpDto,
  ) {
    const asset =
      dto.asset === 'SOL' ? TopUpPaymentAsset.SOL : TopUpPaymentAsset.USDC;
    return this.crypto.createIntent(user.id, asset);
  }

  @Post('crypto/confirm')
  confirmCryptoIntent(
    @CurrentUser() user: AuthUser,
    @Body() dto: ConfirmCryptoTopUpDto,
  ) {
    return this.crypto.confirmIntent(user.id, dto.paymentId, dto.txSignature);
  }
}
