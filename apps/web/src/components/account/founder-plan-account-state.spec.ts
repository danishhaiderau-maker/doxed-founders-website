import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { FounderPlanCatalog, FounderPlanEntitlement } from '@/lib/api';
import {
  canChangeFounderTeamRoles,
  canManageFounderTeam,
  canRemoveFounderTeamMember,
  hasResolvedFounderPlan,
} from './founder-plan-account-state';

const catalog: FounderPlanCatalog = {
  currency: 'usd',
  plans: [
    {
      id: 'free',
      priceCentsMonthly: 0,
      weeklyWeightedUnits: 200_000,
      checkoutAvailable: false,
    },
  ],
};

const entitlement: FounderPlanEntitlement = {
  plan: 'free',
  quotaOwnerKey: 'user:user-1',
  weeklyWeightedUnitCap: 200_000,
  currentPeriodStart: null,
  currentPeriodEnd: null,
  priceCentsMonthly: 0,
  teamId: null,
  teamName: null,
  teamRole: null,
  coordination: false,
  remoteControl: false,
  rolesAndAudit: false,
  requiresXVerification: true,
};

describe('Founder account plan state', () => {
  it('never treats loading, errors, or incomplete payloads as a resolved Free plan', () => {
    assert.equal(hasResolvedFounderPlan('loading', null, null), false);
    assert.equal(hasResolvedFounderPlan('error', catalog, entitlement), false);
    assert.equal(hasResolvedFounderPlan('ready', null, entitlement), false);
    assert.equal(hasResolvedFounderPlan('ready', catalog, null), false);
  });

  it('accepts plan data only after both live contracts resolve', () => {
    assert.equal(hasResolvedFounderPlan('ready', catalog, entitlement), true);
  });
});

describe('Founder Team account permissions', () => {
  it('allows owners and admins to add existing founders', () => {
    assert.equal(canManageFounderTeam('owner'), true);
    assert.equal(canManageFounderTeam('admin'), true);
    assert.equal(canManageFounderTeam('member'), false);
    assert.equal(canManageFounderTeam(null), false);
  });

  it('keeps role changes owner-only', () => {
    assert.equal(canChangeFounderTeamRoles('owner'), true);
    assert.equal(canChangeFounderTeamRoles('admin'), false);
    assert.equal(canChangeFounderTeamRoles('member'), false);
  });

  it('matches server removal rules for owners, admins, and members', () => {
    assert.equal(canRemoveFounderTeamMember('owner', 'OWNER'), false);
    assert.equal(canRemoveFounderTeamMember('owner', 'ADMIN'), true);
    assert.equal(canRemoveFounderTeamMember('owner', 'MEMBER'), true);
    assert.equal(canRemoveFounderTeamMember('admin', 'ADMIN'), false);
    assert.equal(canRemoveFounderTeamMember('admin', 'MEMBER'), true);
    assert.equal(canRemoveFounderTeamMember('member', 'MEMBER'), false);
  });
});
