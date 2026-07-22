import { BadRequestException, ForbiddenException, Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import {
  FounderPlanEntitlementsService,
  type FounderPlanEntitlement,
} from './founder-plan-entitlements.service';

type AssignableTeamRole = 'ADMIN' | 'MEMBER';

@Injectable()
export class FounderPlanTeamService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly entitlements: FounderPlanEntitlementsService,
  ) {}

  async overview(userId: string) {
    const entitlement = await this.requireTeam(userId);
    return this.prisma.founderPlanTeam.findUniqueOrThrow({
      where: { id: entitlement.teamId! },
      include: {
        subscription: true,
        members: {
          include: {
            user: { select: { id: true, email: true, name: true, platformHandle: true, avatarUrl: true } },
          },
          orderBy: [{ role: 'asc' }, { createdAt: 'asc' }],
        },
      },
    });
  }

  async addMember(userId: string, email: string, role: AssignableTeamRole = 'MEMBER') {
    const entitlement = await this.requireManager(userId);
    const nextRole = assignableRole(role);
    if (entitlement.teamRole === 'admin' && nextRole !== 'MEMBER') {
      throw new ForbiddenException('Team admins may add members; only the owner may add another admin.');
    }
    const normalizedEmail = email?.trim().toLowerCase();
    if (!normalizedEmail) throw new BadRequestException('Member email is required.');
    const target = await this.prisma.user.findUnique({ where: { email: normalizedEmail } });
    if (!target) throw new NotFoundException('That founder does not have an account yet.');
    const member = await this.prisma.founderPlanTeamMember.upsert({
      where: { teamId_userId: { teamId: entitlement.teamId!, userId: target.id } },
      create: { teamId: entitlement.teamId!, userId: target.id, role: nextRole },
      update: { role: nextRole },
      include: { user: { select: { id: true, email: true, name: true, platformHandle: true, avatarUrl: true } } },
    });
    await this.audit(userId, entitlement.teamId!, 'TEAM_MEMBER_ADDED', {
      targetUserId: target.id,
      role: nextRole,
    });
    return member;
  }

  async changeRole(userId: string, memberId: string, role: AssignableTeamRole) {
    const entitlement = await this.requireTeam(userId);
    if (entitlement.teamRole !== 'owner') {
      throw new ForbiddenException('Only the Team owner may change roles.');
    }
    const nextRole = assignableRole(role);
    const member = await this.memberInTeam(entitlement.teamId!, memberId);
    if (member.role === 'OWNER') throw new ForbiddenException('The Team owner role cannot be reassigned here.');
    const updated = await this.prisma.founderPlanTeamMember.update({
      where: { id: member.id },
      data: { role: nextRole },
    });
    await this.audit(userId, entitlement.teamId!, 'TEAM_ROLE_CHANGED', {
      targetUserId: member.userId,
      from: member.role,
      to: nextRole,
    });
    return updated;
  }

  async removeMember(userId: string, memberId: string) {
    const entitlement = await this.requireManager(userId);
    const member = await this.memberInTeam(entitlement.teamId!, memberId);
    if (member.role === 'OWNER') throw new ForbiddenException('The Team owner cannot be removed.');
    if (entitlement.teamRole === 'admin' && member.role !== 'MEMBER') {
      throw new ForbiddenException('Team admins may remove members; only the owner may remove an admin.');
    }
    await this.prisma.founderPlanTeamMember.delete({ where: { id: member.id } });
    await this.audit(userId, entitlement.teamId!, 'TEAM_MEMBER_REMOVED', {
      targetUserId: member.userId,
      role: member.role,
    });
    return { removed: true, memberId };
  }

  private async memberInTeam(teamId: string, memberId: string) {
    const member = await this.prisma.founderPlanTeamMember.findUnique({ where: { id: memberId } });
    if (!member || member.teamId !== teamId) throw new NotFoundException('Team member not found.');
    return member;
  }

  private async requireManager(userId: string): Promise<FounderPlanEntitlement> {
    const entitlement = await this.requireTeam(userId);
    if (entitlement.teamRole !== 'owner' && entitlement.teamRole !== 'admin') {
      throw new ForbiddenException('Team owner or admin access is required.');
    }
    return entitlement;
  }

  private async requireTeam(userId: string): Promise<FounderPlanEntitlement> {
    const entitlement = await this.entitlements.resolve(userId);
    if (entitlement.plan !== 'team' || !entitlement.teamId || !entitlement.teamRole) {
      throw new ForbiddenException('An active Founder Team plan is required.');
    }
    return entitlement;
  }

  private audit(actorUserId: string, teamId: string, action: string, details: Record<string, string>) {
    return this.prisma.founderCoordinationAudit.create({
      data: { actorUserId, teamId, action, details },
    });
  }
}

function assignableRole(role: unknown): AssignableTeamRole {
  if (role !== 'ADMIN' && role !== 'MEMBER') {
    throw new BadRequestException('Role must be ADMIN or MEMBER.');
  }
  return role;
}
