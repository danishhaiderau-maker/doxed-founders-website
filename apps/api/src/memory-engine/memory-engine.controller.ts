import {
  Controller,
  Get,
  Query,
  Req,
  UseGuards,
} from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import {
  FounderNodeGuard,
  type FounderNodeRequestUser,
} from '../founder-node/founder-node.guard';
import { MemoryEngineService } from './memory-engine.service';

/**
 * Memory Engine — IDE context endpoint.
 *
 * `GET /api/memory/context?projectId=<workspace-name>`
 *
 * Used by the Founder IDE chat extension (Phase 4) to inject project + founder
 * memory into the system prompt before each chat request. Auth is via the
 * Founder Node bearer token (same as `/api/v1/chat/completions`).
 *
 * Backed by Prisma memory stores (conversation / project / founder / workspace)
 * in `memory-engine.service.ts`. Empty scopes return empty arrays so chat
 * never breaks when a founder has not written memory yet.
 */
@Public()
@Controller('memory')
export class MemoryEngineController {
  constructor(private readonly memory: MemoryEngineService) {}

  @UseGuards(FounderNodeGuard)
  @Get('context')
  async context(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Query('projectId') projectId?: string,
  ): Promise<{
    projectId?: string;
    userId: string;
    projectMemory: unknown[];
    founderMemory: unknown[];
    systemPromptHint?: string;
  }> {
    const userId = req.founderNode.userId;
    const pid = (projectId ?? '').trim() || undefined;

    // Project memory is scoped to the workspace; founder memory is scoped to
    // the user. Both are best-effort — stubbed backend returns [] today.
    const projectMemory = pid
      ? await this.memory.query({ store: 'project', scope: pid, limit: 30 })
      : [];
    const founderMemory = await this.memory.query({
      store: 'founder',
      scope: userId,
      limit: 30,
    });

    return {
      projectId: pid,
      userId,
      projectMemory,
      founderMemory,
    };
  }
}
