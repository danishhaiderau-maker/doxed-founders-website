import {
  BadRequestException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { Prisma, TopUpPaymentAsset, TopUpPaymentStatus } from '@prisma/client';
import { randomBytes } from 'node:crypto';
import {
  TOP_UP_FEE_USD,
  TOP_UP_INTENT_TTL_MS,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { verifySolanaTopUpPayment } from '../payments/solana-tx-verify';
import { PaperTradingService } from './paper-trading.service';

@Injectable()
export class PaperTradingCryptoService {
  private readonly logger = new Logger(PaperTradingCryptoService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly paperTrading: PaperTradingService,
  ) {}

  isEnabled(): boolean {
    return Boolean(
      process.env.SOLANA_RPC_URL?.trim() &&
        this.getRpcUrl(),
    );
  }

  async createIntent(userId: string, asset: TopUpPaymentAsset = TopUpPaymentAsset.USDC) {
    if (!this.isEnabled()) {
      throw new BadRequestException(
        'On-chain top-up is not configured. Set SOLANA_RPC_URL and admin treasury address.',
      );
    }

    await this.paperTrading.assertRestrictedForTopUp(userId);

    const [treasury, wallet] = await Promise.all([
      this.prisma.platformTreasury.findUnique({ where: { id: 'default' } }),
      this.prisma.walletConnection.findFirst({
        where: { userId, chain: 'SOLANA' },
      }),
    ]);

    const treasuryAddress = treasury?.solanaTreasuryAddress?.trim();
    if (!treasuryAddress) {
      throw new BadRequestException(
        'Platform Solana treasury is not configured. Contact admin.',
      );
    }
    if (!wallet) {
      throw new BadRequestException(
        'Connect your Solana wallet in Account → Security before paying on-chain.',
      );
    }

    await this.prisma.topUpPayment.updateMany({
      where: {
        userId,
        status: TopUpPaymentStatus.PENDING,
        expiresAt: { lt: new Date() },
      },
      data: { status: TopUpPaymentStatus.EXPIRED },
    });

    const reference = randomBytes(4).toString('hex').toUpperCase();
    const expiresAt = new Date(Date.now() + TOP_UP_INTENT_TTL_MS);

    const payment = await this.prisma.topUpPayment.create({
      data: {
        userId,
        reference,
        asset,
        amountUsd: new Prisma.Decimal(TOP_UP_FEE_USD),
        treasuryAddress,
        payerAddress: wallet.address,
        status: TopUpPaymentStatus.PENDING,
        expiresAt,
      },
    });

    return {
      paymentId: payment.id,
      reference: payment.reference,
      asset: payment.asset,
      amountUsd: TOP_UP_FEE_USD,
      treasuryAddress,
      payerAddress: wallet.address,
      memo: `DCF-${reference}`,
      expiresAt: expiresAt.toISOString(),
      instructions:
        asset === TopUpPaymentAsset.USDC
          ? `Send exactly $${TOP_UP_FEE_USD} USDC from ${wallet.address} to ${treasuryAddress}. Optional memo: DCF-${reference}`
          : `Send at least ${TOP_UP_FEE_USD} SOL from ${wallet.address} to ${treasuryAddress}. Optional memo: DCF-${reference}`,
    };
  }

  async confirmIntent(userId: string, paymentId: string, txSignature: string) {
    const signature = txSignature.trim();
    if (!signature) {
      throw new BadRequestException('Transaction signature is required');
    }

    const payment = await this.prisma.topUpPayment.findUnique({
      where: { id: paymentId },
    });
    if (!payment || payment.userId !== userId) {
      throw new NotFoundException('Top-up payment not found');
    }
    if (payment.status === TopUpPaymentStatus.CONFIRMED) {
      return {
        success: true,
        alreadyConfirmed: true,
        message: 'Payment already credited.',
      };
    }
    if (payment.status !== TopUpPaymentStatus.PENDING) {
      throw new BadRequestException(`Payment is ${payment.status.toLowerCase()}`);
    }
    if (payment.expiresAt.getTime() < Date.now()) {
      await this.prisma.topUpPayment.update({
        where: { id: payment.id },
        data: { status: TopUpPaymentStatus.EXPIRED },
      });
      throw new BadRequestException('Payment intent expired — create a new one.');
    }

    const wallet = await this.prisma.walletConnection.findFirst({
      where: { userId, chain: 'SOLANA' },
    });
    if (!wallet || wallet.address !== payment.payerAddress) {
      throw new BadRequestException(
        'Linked Solana wallet must match the wallet used when creating this payment.',
      );
    }

    const existingTx = await this.prisma.topUpPayment.findFirst({
      where: { txSignature: signature, status: TopUpPaymentStatus.CONFIRMED },
    });
    if (existingTx) {
      throw new BadRequestException('This transaction was already used for a top-up.');
    }

    const verification = await verifySolanaTopUpPayment({
      rpcUrl: this.getRpcUrl(),
      txSignature: signature,
      treasuryAddress: payment.treasuryAddress,
      expectedPayerAddress: payment.payerAddress!,
      minAmountUsd: TOP_UP_FEE_USD,
      asset: payment.asset,
    });

    if (!verification.ok) {
      await this.prisma.topUpPayment.update({
        where: { id: payment.id },
        data: { status: TopUpPaymentStatus.FAILED },
      });
      throw new BadRequestException(verification.reason ?? 'On-chain payment verification failed');
    }

    await this.prisma.$transaction(async (tx) => {
      await tx.topUpPayment.update({
        where: { id: payment.id },
        data: {
          status: TopUpPaymentStatus.CONFIRMED,
          txSignature: signature,
          payerAddress: verification.payerAddress ?? payment.payerAddress,
          confirmedAt: new Date(),
        },
      });
    });

    const result = await this.paperTrading.resetPortfolio(userId, 'crypto');
    this.logger.log(
      `Crypto top-up confirmed for ${userId} ref=${payment.reference} tx=${signature}`,
    );

    return {
      ...result,
      paymentId: payment.id,
      reference: payment.reference,
      txSignature: signature,
      payerAddress: verification.payerAddress,
    };
  }

  private getRpcUrl(): string {
    return (
      process.env.SOLANA_RPC_URL?.trim() ||
      process.env.HELIUS_RPC_URL?.trim() ||
      'https://api.mainnet-beta.solana.com'
    );
  }
}
