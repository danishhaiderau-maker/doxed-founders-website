import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import {
  EMPTY_WORKSPACE_SESSION,
  WorkspaceSessionShape,
} from '../workspace-session/workspace-session.service';

export type ConnectedWorkspaceCreateInput = {
  label: string;
  repository?: string | null;
  branch?: string | null;
  ideProvider?: string | null;
  aiProvider?: string | null;
};

export type ConnectedWorkspaceUpdateInput = Partial<ConnectedWorkspaceCreateInput>;

const ALLOWED_PATCH_KEYS: ReadonlySet<string> = new Set([
  'label',
  'repository',
  'branch',
  'ideProvider',
  'aiProvider',
]);

function coerceCreateInput(
  raw: Record<string, unknown>,
): ConnectedWorkspaceCreateInput {
  const label = raw.label;
  if (typeof label !== 'string' || label.trim().length === 0) {
    throw new BadRequestException('label must be a non-empty string');
  }
  const pick = (key: string): string | null | undefined => {
    if (!(key in raw)) return undefined;
    const value = raw[key];
    if (value === null) return null;
    if (typeof value === 'string') return value;
    throw new BadRequestException(`${key} must be a string or null`);
  };

  return {
    label: label.trim(),
    repository: pick('repository'),
    branch: pick('branch'),
    ideProvider: pick('ideProvider'),
    aiProvider: pick('aiProvider'),
  };
}

function coerceUpdateInput(
  raw: Record<string, unknown>,
): ConnectedWorkspaceUpdateInput {
  const data: ConnectedWorkspaceUpdateInput = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!ALLOWED_PATCH_KEYS.has(key)) continue;
    if (value === undefined) continue;
    if (value === null) {
      (data as Record<string, unknown>)[key] = null;
    } else if (typeof value === 'string') {
      if (key === 'label' && value.trim().length === 0) {
        throw new BadRequestException('label must be a non-empty string');
      }
      (data as Record<string, unknown>)[key] =
        key === 'label' ? value.trim() : value;
    } else {
      throw new BadRequestException(`${key} must be a string or null`);
    }
  }
  return data;
}

@Injectable()
export class ConnectedWorkspaceService {
  constructor(private readonly prisma: PrismaService) {}

  async listForUser(userId: string) {
    return this.prisma.connectedWorkspace.findMany({
      where: { userId },
      orderBy: { lastActiveAt: 'desc' },
      take: 10,
    });
  }

  async create(userId: string, raw: Record<string, unknown>) {
    const input = coerceCreateInput(raw);
    const data: Prisma.ConnectedWorkspaceUncheckedCreateInput = {
      userId,
      label: input.label,
      repository: input.repository ?? null,
      branch: input.branch ?? null,
      ideProvider: input.ideProvider ?? null,
      aiProvider: input.aiProvider ?? null,
    };
    return this.prisma.connectedWorkspace.create({ data });
  }

  async update(userId: string, id: string, raw: Record<string, unknown>) {
    const data = coerceUpdateInput(raw);
    if (Object.keys(data).length === 0) {
      throw new BadRequestException('No updatable fields supplied');
    }
    const existing = await this.prisma.connectedWorkspace.findFirst({
      where: { id, userId },
      select: { id: true },
    });
    if (!existing) throw new NotFoundException('Workspace not found');
    return this.prisma.connectedWorkspace.update({ where: { id }, data });
  }

  async delete(userId: string, id: string): Promise<void> {
    const existing = await this.prisma.connectedWorkspace.findFirst({
      where: { id, userId },
      select: { id: true },
    });
    if (!existing) throw new NotFoundException('Workspace not found');
    // Sessions referencing this workspace fall back to workspaceId = null (SetNull).
    await this.prisma.connectedWorkspace.delete({ where: { id } });
  }

  /**
   * Find or create the WorkspaceSession row for a given workspace. A workspace always
   * has at most one session row; we find-first then create if missing (no @unique on
   * (userId, workspaceId) so upsert-by-key is not available).
   */
  async getOrCreateSession(
    userId: string,
    workspaceId: string,
  ): Promise<WorkspaceSessionShape> {
    const workspace = await this.prisma.connectedWorkspace.findFirst({
      where: { id: workspaceId, userId },
      select: { id: true },
    });
    if (!workspace) throw new NotFoundException('Workspace not found');

    const existing = await this.prisma.workspaceSession.findFirst({
      where: { workspaceId, userId },
    });
    if (existing) return this.mapSessionRow(existing);

    const row = await this.prisma.workspaceSession.create({
      data: { userId, workspaceId, conversation: [], terminalScrollback: [], openFiles: [] },
    });
    return this.mapSessionRow(row);
  }

  private mapSessionRow(row: {
    selectedAiProvider: string | null;
    selectedModelKey: string | null;
    conversation: Prisma.JsonValue;
    terminalScrollback: Prisma.JsonValue;
    openFiles: Prisma.JsonValue;
    activeNav: string | null;
    panelState: Prisma.JsonValue;
    updatedAt: Date;
  }): WorkspaceSessionShape {
    // Reuse the existing mapper shape contract.
    const empty = { ...EMPTY_WORKSPACE_SESSION };
    return {
      selectedAiProvider: row.selectedAiProvider ?? empty.selectedAiProvider,
      selectedModelKey: row.selectedModelKey ?? empty.selectedModelKey,
      conversation: arrayFromJson(row.conversation) as never,
      terminalScrollback: arrayFromJson(row.terminalScrollback) as never,
      openFiles: arrayFromJson(row.openFiles).filter(
        (v): v is string => typeof v === 'string',
      ),
      activeNav: row.activeNav ?? empty.activeNav,
      panelState: empty.panelState,
      updatedAt: row.updatedAt.toISOString(),
    };
  }
}

function arrayFromJson(value: Prisma.JsonValue): unknown[] {
  if (Array.isArray(value)) return value as unknown[];
  return [];
}
