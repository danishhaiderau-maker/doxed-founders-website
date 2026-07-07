import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import type { ExecutionProfile } from '../capability-registry/capability-registry.types';

const VALID_PROFILES: ExecutionProfile[] = [
  'turbo',
  'balanced',
  'architect',
  'autonomous',
];

/**
 * Workspace-scoped Execution Profile resolver. See docs/KERNEL.md §7.
 */
@Injectable()
export class ExecutionProfileService {
  constructor(private readonly prisma: PrismaService) {}

  async getProfile(workspaceId?: string | null): Promise<ExecutionProfile> {
    if (!workspaceId) return 'balanced';
    const row = await this.prisma.workspaceExecutionProfile.findUnique({
      where: { workspaceId },
    });
    if (!row) return 'balanced';
    return VALID_PROFILES.includes(row.profile as ExecutionProfile)
      ? (row.profile as ExecutionProfile)
      : 'balanced';
  }

  async setProfile(
    workspaceId: string,
    profile: ExecutionProfile,
  ): Promise<void> {
    await this.prisma.workspaceExecutionProfile.upsert({
      where: { workspaceId },
      create: { workspaceId, profile },
      update: { profile },
    });
  }
}
