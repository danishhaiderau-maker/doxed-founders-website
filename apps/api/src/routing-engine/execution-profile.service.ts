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
 *
 * KNOWN LIMITATION: `@prisma/client` has not yet been regenerated to include
 * the `WorkspaceExecutionProfile` model. We access it through `any` casts
 * until the parent agent runs `prisma generate`.
 */
@Injectable()
export class ExecutionProfileService {
  constructor(private readonly prisma: PrismaService) {}

  async getProfile(workspaceId?: string | null): Promise<ExecutionProfile> {
    if (!workspaceId) return 'balanced';
    const row = await (this.prisma as any).workspaceExecutionProfile.findUnique({
      where: { workspaceId },
    });
    if (!row) return 'balanced';
    return VALID_PROFILES.includes(row.profile) ? row.profile : 'balanced';
  }

  async setProfile(
    workspaceId: string,
    profile: ExecutionProfile,
  ): Promise<void> {
    await (this.prisma as any).workspaceExecutionProfile.upsert({
      where: { workspaceId },
      create: { workspaceId, profile },
      update: { profile },
    });
  }
}
