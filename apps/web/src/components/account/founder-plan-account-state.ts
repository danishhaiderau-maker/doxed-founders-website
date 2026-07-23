import type {
  FounderPlanCatalog,
  FounderPlanEntitlement,
  FounderTeamRole,
} from '@/lib/api';

export type FounderPlanLoadState = 'loading' | 'ready' | 'error';

export function hasResolvedFounderPlan(
  state: FounderPlanLoadState,
  catalog: FounderPlanCatalog | null,
  entitlement: FounderPlanEntitlement | null,
): entitlement is FounderPlanEntitlement {
  return state === 'ready' && catalog !== null && entitlement !== null;
}

export function canManageFounderTeam(role: FounderPlanEntitlement['teamRole']): boolean {
  return role === 'owner' || role === 'admin';
}

export function canChangeFounderTeamRoles(role: FounderPlanEntitlement['teamRole']): boolean {
  return role === 'owner';
}

export function canRemoveFounderTeamMember(
  actorRole: FounderPlanEntitlement['teamRole'],
  targetRole: FounderTeamRole,
): boolean {
  if (targetRole === 'OWNER') return false;
  if (actorRole === 'owner') return true;
  return actorRole === 'admin' && targetRole === 'MEMBER';
}
