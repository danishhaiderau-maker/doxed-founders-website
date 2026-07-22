import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  FounderPlanBillingService,
  founderPlanStatusFromStripe,
} from './founder-plan-billing.service';

describe('Founder plan billing', () => {
  it('maps Stripe lifecycle states without granting canceled access', () => {
    assert.equal(founderPlanStatusFromStripe('active'), 'ACTIVE');
    assert.equal(founderPlanStatusFromStripe('trialing'), 'ACTIVE');
    assert.equal(founderPlanStatusFromStripe('past_due'), 'PAST_DUE');
    assert.equal(founderPlanStatusFromStripe('unpaid'), 'PAST_DUE');
    assert.equal(founderPlanStatusFromStripe('canceled'), 'CANCELED');
  });

  it('keeps Team checkout unavailable until price and pool are approved', () => {
    const service = new FounderPlanBillingService({} as never);
    const team = service.catalog().plans.find((plan) => plan.id === 'team');
    assert.equal(team?.checkoutAvailable, false);
    assert.equal(team?.priceCentsMonthly, null);
    assert.equal(team?.weeklyWeightedUnits, null);
  });

  it('uses the published Builder price and allowance', () => {
    const service = new FounderPlanBillingService({} as never);
    const builder = service.catalog().plans.find((plan) => plan.id === 'builder');
    assert.equal(builder?.priceCentsMonthly, 3_500);
    assert.equal(builder?.weeklyWeightedUnits, 5_000_000);
  });
});
