import { BadRequestException, Injectable } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

export type WorkspaceConversationMessage = {
  role: 'user' | 'agent';
  text: string;
  model?: string;
  ts?: string;
  attachments?: { name: string }[];
};

export type WorkspaceTerminalLine = {
  ts: string;
  line: string;
  stream?: string;
};

export type WorkspacePanelState = {
  terminalOpen?: boolean;
  terminalHeight?: number;
  sidebarOpen?: boolean;
};

export type WorkspaceSessionShape = {
  selectedAiProvider: string | null;
  selectedModelKey: string | null;
  conversation: WorkspaceConversationMessage[];
  terminalScrollback: WorkspaceTerminalLine[];
  openFiles: string[];
  activeNav: string | null;
  panelState: WorkspacePanelState;
  updatedAt: string | null;
};

export const EMPTY_WORKSPACE_SESSION: WorkspaceSessionShape = {
  selectedAiProvider: null,
  selectedModelKey: null,
  conversation: [],
  terminalScrollback: [],
  openFiles: [],
  activeNav: null,
  panelState: {
    terminalOpen: true,
    terminalHeight: 180,
    sidebarOpen: true,
  },
  updatedAt: null,
};

const SCALAR_KEYS = new Set([
  'selectedAiProvider',
  'selectedModelKey',
  'activeNav',
]);

const JSON_KEYS = new Set(['conversation', 'terminalScrollback', 'openFiles', 'panelState']);

function coercePatch(patch: Record<string, unknown>): Prisma.WorkspaceSessionUpdateInput {
  const data: Prisma.WorkspaceSessionUpdateInput = {};
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    if (SCALAR_KEYS.has(key)) {
      if (value === null) {
        // Prisma nullable scalar — set to null explicitly
        (data as Record<string, unknown>)[key] = null;
      } else if (typeof value === 'string') {
        (data as Record<string, unknown>)[key] = value;
      } else {
        throw new BadRequestException(`${key} must be a string or null`);
      }
    } else if (JSON_KEYS.has(key)) {
      if (value === null) {
        (data as Record<string, unknown>)[key] = Prisma.DbNull;
      } else if (typeof value === 'object') {
        (data as Record<string, unknown>)[key] = value as Prisma.InputJsonValue;
      } else {
        throw new BadRequestException(`${key} must be a JSON object or null`);
      }
    } else {
      // Ignore unknown keys — never overwrite unrelated fields.
    }
  }
  return data;
}

@Injectable()
export class WorkspaceSessionService {
  constructor(private readonly prisma: PrismaService) {}

  async getForUser(userId: string): Promise<WorkspaceSessionShape> {
    const row = await this.prisma.workspaceSession.findUnique({ where: { userId } });
    if (!row) return { ...EMPTY_WORKSPACE_SESSION };
    return this.mapRow(row);
  }

  async patchForUser(
    userId: string,
    patch: Record<string, unknown>,
  ): Promise<WorkspaceSessionShape> {
    const data = coercePatch(patch);
    const row = await this.prisma.workspaceSession.upsert({
      where: { userId },
      create: {
        userId,
        ...this.createPayloadFromPatch(patch),
      },
      update: data,
    });
    return this.mapRow(row);
  }

  private createPayloadFromPatch(patch: Record<string, unknown>): Prisma.WorkspaceSessionCreateInput {
    const payload: Prisma.WorkspaceSessionCreateInput = { userId };
    for (const [key, value] of Object.entries(patch)) {
      if (value === undefined) continue;
      if (SCALAR_KEYS.has(key)) {
        if (value === null) {
          (payload as Record<string, unknown>)[key] = null;
        } else if (typeof value === 'string') {
          (payload as Record<string, unknown>)[key] = value;
        }
      } else if (JSON_KEYS.has(key) && typeof value === 'object' && value !== null) {
        (payload as Record<string, unknown>)[key] = value as Prisma.InputJsonValue;
      }
    }
    return payload;
  }

  private mapRow(row: {
    selectedAiProvider: string | null;
    selectedModelKey: string | null;
    conversation: Prisma.JsonValue;
    terminalScrollback: Prisma.JsonValue;
    openFiles: Prisma.JsonValue;
    activeNav: string | null;
    panelState: Prisma.JsonValue;
    updatedAt: Date;
  }): WorkspaceSessionShape {
    return {
      selectedAiProvider: row.selectedAiProvider,
      selectedModelKey: row.selectedModelKey,
      conversation: asArray(row.conversation),
      terminalScrollback: asArray(row.terminalScrollback),
      openFiles: asArray(row.openFiles).filter((v): v is string => typeof v === 'string'),
      activeNav: row.activeNav,
      panelState: asPanelState(row.panelState),
      updatedAt: row.updatedAt.toISOString(),
    };
  }
}

function asArray(value: Prisma.JsonValue): unknown[] {
  if (Array.isArray(value)) return value as unknown[];
  return [];
}

function asPanelState(value: Prisma.JsonValue): WorkspacePanelState {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>;
    return {
      terminalOpen: typeof obj.terminalOpen === 'boolean' ? obj.terminalOpen : true,
      terminalHeight: typeof obj.terminalHeight === 'number' ? obj.terminalHeight : 180,
      sidebarOpen: typeof obj.sidebarOpen === 'boolean' ? obj.sidebarOpen : true,
    };
  }
  return { terminalOpen: true, terminalHeight: 180, sidebarOpen: true };
}
