import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import Stripe from 'stripe';
import { PrismaService } from '../prisma/prisma.service';

export function founderPlanStatusFromStripe(
  status: Stripe.Subscription.Status,
): 'ACTIVE' | 'PAST_DUE' | 'CANCELED' {
  if (status === 'active' || status === 'trialing') return 'ACTIVE';
  if (status === 'past_due' || status === 'unpaid') return 'PAST_DUE';
  return 'CANCELED';
}

@Injectable()
export class FounderPlanBillingService {
  private readonly logger = new Logger(FounderPlanBillingService.name);
  private readonly stripe: Stripe | null;

  constructor(private readonly prisma: PrismaService) {
    const secret = process.env.STRIPE_SECRET_KEY?.trim();
    this.stripe = secret ? new Stripe(secret) : null;
  }

  catalog() {
    return {
      currency: 'usd',
      plans: [
        {
          id: 'free',
          priceCentsMonthly: 0,
          weeklyWeightedUnits: 200_000,
          checkoutAvailable: false,
        },
        {
          id: 'builder',
          priceCentsMonthly: 3_500,
          weeklyWeightedUnits: 5_000_000,
          checkoutAvailable: Boolean(
            this.stripe && process.env.STRIPE_FOUNDER_BUILDER_PRICE_ID?.trim(),
          ),
        },
        {
          id: 'team',
          priceCentsMonthly: null,
          weeklyWeightedUnits: null,
          checkoutAvailable: false,
          message: 'Team price and shared allowance are configured per agreement.',
        },
      ],
    };
  }

  async createBuilderCheckout(userId: string) {
    if (!this.stripe) {
      throw new BadRequestException('Founder billing is not configured yet.');
    }
    const price = process.env.STRIPE_FOUNDER_BUILDER_PRICE_ID?.trim();
    if (!price) {
      throw new BadRequestException('Founder Builder checkout is not configured yet.');
    }
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { email: true },
    });
    if (!user) throw new BadRequestException('Founder account was not found.');

    const existing = await this.prisma.founderPlanSubscription.findUnique({
      where: { userId },
    });
    const origin = (process.env.NEXTAUTH_URL ?? 'http://localhost:3000').replace(/\/$/, '');
    const session = await this.stripe.checkout.sessions.create({
      mode: 'subscription',
      line_items: [{ price, quantity: 1 }],
      ...(existing?.stripeCustomerId
        ? { customer: existing.stripeCustomerId }
        : { customer_email: user.email }),
      metadata: { type: 'founder_builder', userId },
      subscription_data: { metadata: { type: 'founder_builder', userId } },
      allow_promotion_codes: true,
      success_url: `${origin}/founder-den?billing=success`,
      cancel_url: `${origin}/founder-den?billing=cancelled`,
    });
    if (!session.url) throw new BadRequestException('Could not create Founder checkout.');
    return { url: session.url, sessionId: session.id };
  }

  async createPortal(userId: string) {
    if (!this.stripe) throw new BadRequestException('Founder billing is not configured yet.');
    const subscription = await this.prisma.founderPlanSubscription.findUnique({
      where: { userId },
    });
    if (!subscription?.stripeCustomerId) {
      throw new BadRequestException('No Founder billing account is connected.');
    }
    const origin = (process.env.NEXTAUTH_URL ?? 'http://localhost:3000').replace(/\/$/, '');
    const session = await this.stripe.billingPortal.sessions.create({
      customer: subscription.stripeCustomerId,
      return_url: `${origin}/founder-den`,
    });
    return { url: session.url };
  }

  async handleWebhook(rawBody: Buffer, signature: string | undefined) {
    if (!this.stripe) throw new BadRequestException('Founder billing is not configured.');
    const secret = (
      process.env.STRIPE_FOUNDER_WEBHOOK_SECRET ?? process.env.STRIPE_WEBHOOK_SECRET
    )?.trim();
    if (!secret) throw new BadRequestException('Founder billing webhook is not configured.');
    if (!signature) throw new BadRequestException('Missing Stripe signature header.');

    let event: Stripe.Event;
    try {
      event = this.stripe.webhooks.constructEvent(rawBody, signature, secret);
    } catch (error) {
      throw new BadRequestException(
        `Stripe webhook error: ${error instanceof Error ? error.message : 'invalid signature'}`,
      );
    }

    if (
      event.type === 'customer.subscription.created' ||
      event.type === 'customer.subscription.updated' ||
      event.type === 'customer.subscription.deleted'
    ) {
      await this.syncBuilderSubscription(event.data.object as Stripe.Subscription);
    }
    return { received: true };
  }

  private async syncBuilderSubscription(subscription: Stripe.Subscription): Promise<void> {
    const userId = subscription.metadata.userId?.trim();
    const existing = await this.prisma.founderPlanSubscription.findUnique({
      where: { stripeSubscriptionId: subscription.id },
    });
    const ownerUserId = userId || existing?.userId;
    if (!ownerUserId) {
      this.logger.warn(`Ignoring Founder subscription ${subscription.id} without userId metadata.`);
      return;
    }
    const item = subscription.items.data[0];
    if (!item) {
      this.logger.warn(`Ignoring Founder subscription ${subscription.id} without a billing item.`);
      return;
    }
    const customerId = typeof subscription.customer === 'string'
      ? subscription.customer
      : subscription.customer.id;
    await this.prisma.founderPlanSubscription.upsert({
      where: { userId: ownerUserId },
      create: {
        userId: ownerUserId,
        tier: 'BUILDER',
        status: founderPlanStatusFromStripe(subscription.status),
        currentPeriodStart: new Date(item.current_period_start * 1_000),
        currentPeriodEnd: new Date(item.current_period_end * 1_000),
        cancelAtPeriodEnd: subscription.cancel_at_period_end,
        stripeCustomerId: customerId,
        stripeSubscriptionId: subscription.id,
      },
      update: {
        tier: 'BUILDER',
        status: founderPlanStatusFromStripe(subscription.status),
        currentPeriodStart: new Date(item.current_period_start * 1_000),
        currentPeriodEnd: new Date(item.current_period_end * 1_000),
        cancelAtPeriodEnd: subscription.cancel_at_period_end,
        stripeCustomerId: customerId,
        stripeSubscriptionId: subscription.id,
      },
    });
  }
}
