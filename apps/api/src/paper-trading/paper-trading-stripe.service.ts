import {
  BadRequestException,
  Injectable,
  Logger,
} from '@nestjs/common';
import Stripe from 'stripe';
import { PaperTradingService } from './paper-trading.service';

const RESET_FEE_CENTS = 5000;

@Injectable()
export class PaperTradingStripeService {
  private readonly logger = new Logger(PaperTradingStripeService.name);
  private readonly stripe: Stripe | null;

  constructor(private readonly paperTrading: PaperTradingService) {
    const secret = process.env.STRIPE_SECRET_KEY?.trim();
    this.stripe = secret ? new Stripe(secret) : null;
  }

  isEnabled(): boolean {
    return Boolean(this.stripe);
  }

  async createResetCheckout(userId: string) {
    if (!this.stripe) {
      throw new BadRequestException(
        'Stripe is not configured. Use dev reset or add STRIPE_SECRET_KEY to .env.',
      );
    }

    await this.paperTrading.assertBustedForReset(userId);

    const webUrl = (process.env.NEXTAUTH_URL ?? 'http://localhost:3000').replace(
      /\/$/,
      '',
    );

    const session = await this.stripe.checkout.sessions.create({
      mode: 'payment',
      line_items: [
        {
          price_data: {
            currency: 'usd',
            unit_amount: RESET_FEE_CENTS,
            product_data: {
              name: 'Paper Trading Restart Penalty',
              description:
                'You went bust. Pay the fine to unlock a fresh $10,000 virtual portfolio.',
            },
          },
          quantity: 1,
        },
      ],
      metadata: {
        userId,
        type: 'paper_reset',
      },
      success_url: `${webUrl}/paper-trading?reset=success`,
      cancel_url: `${webUrl}/paper-trading?reset=cancelled`,
    });

    if (!session.url) {
      throw new BadRequestException('Could not create Stripe checkout session');
    }

    return { url: session.url, sessionId: session.id };
  }

  async handleWebhook(rawBody: Buffer, signature: string | undefined) {
    if (!this.stripe) {
      throw new BadRequestException('Stripe is not configured');
    }

    const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET?.trim();
    if (!webhookSecret) {
      throw new BadRequestException('STRIPE_WEBHOOK_SECRET is not configured');
    }
    if (!signature) {
      throw new BadRequestException('Missing Stripe signature header');
    }

    let event: Stripe.Event;
    try {
      event = this.stripe.webhooks.constructEvent(
        rawBody,
        signature,
        webhookSecret,
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Invalid signature';
      throw new BadRequestException(`Stripe webhook error: ${message}`);
    }

    if (event.type === 'checkout.session.completed') {
      const session = event.data.object as Stripe.Checkout.Session;
      if (session.metadata?.type === 'paper_reset' && session.metadata.userId) {
        await this.paperTrading.resetPortfolio(session.metadata.userId, 'stripe');
        this.logger.log(`Portfolio reset after Stripe payment for ${session.metadata.userId}`);
      }
    }

    return { received: true };
  }
}
