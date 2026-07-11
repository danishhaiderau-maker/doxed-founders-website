import {
  BadRequestException,
  Body,
  Controller,
  ForbiddenException,
  Get,
  HttpCode,
  Injectable,
  Logger,
  NotFoundException,
  Param,
  Post,
  Query,
} from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { LamOrchestratorService } from './lam-orchestrator.service';
import { ComputerUseAdapter, COMPUTER_USE_TIER_MESSAGE } from './computer-use.adapter';
import type { LamAdapterId, LamTask } from './lam.types';

/**
 * LAM controller — the REST surface for the Large Action Model layer.
 *
 * Endpoints:
 *   POST /api/lam/task            submit a natural-language task
 *   GET  /api/lam/task/:id        status + result + step log
 *   GET  /api/lam/tasks           recent tasks for the history list
 *   GET  /api/lam/adapters        which adapters are available
 *
 * Auth: the global JwtAuthGuard enforces a valid JWT on every route.
 * Computer-Use tasks are additionally gated to BuilderTier.VERIFIED_BUILDER
 * (the "Doxxed" tier) — checked here at the controller layer, and again
 * at the adapter layer via COMPUTER_USE_ENABLED.
 */
@Controller('lam')
@Injectable()
export class LamController {
  private readonly logger = new Logger(LamController.name);

  constructor(
    private readonly orchestrator: LamOrchestratorService,
    private readonly computerUse: ComputerUseAdapter,
    private readonly prisma: PrismaService,
  ) {}

  /**
   * POST /api/lam/task — submit a natural-language task.
   * Returns the new task (status PLANNING); the client polls
   * GET /api/lam/task/:id for progress.
   */
  @Post('task')
  @HttpCode(202)
  async submitTask(
    @Body() body: SubmitLamTaskDto,
    @CurrentUser() user: AuthUser,
  ): Promise<LamTask> {
    const goal = body?.goal?.trim();
    if (!goal || goal.length < 8) {
      throw new BadRequestException('goal must be at least 8 characters');
    }
    if (goal.length > 1000) {
      throw new BadRequestException('goal must be 1000 characters or fewer');
    }

    // Pre-check: if the goal mentions desktop/computer-use, enforce the
    // Doxxed tier gate up front so the founder gets a clear 403 rather
    // than a FAILED task after planning. The orchestrator re-checks at
    // step time as defense in depth.
    if (/\b(computer|desktop|screen|window)\b/i.test(goal)) {
      await this.requireDoxxedTier(user.id);
      if (!this.computerUse.isEnabled()) {
        throw new ForbiddenException(
          `Computer Use premium tier is not enabled on this server. ${COMPUTER_USE_TIER_MESSAGE}`,
        );
      }
    }

    this.logger.log(`LAM task submitted by user=${user.id}: "${goal.slice(0, 80)}"`);
    return this.orchestrator.submitTask({ userId: user.id, nodeId: 'api' }, goal);
  }

  /**
   * GET /api/lam/task/:id — full task including step log + result.
   */
  @Get('task/:id')
  async getTask(@Param('id') id: string, @CurrentUser() user: AuthUser): Promise<LamTask> {
    const task = await this.orchestrator.getTask(user.id, id);
    if (!task) throw new NotFoundException(`LAM task ${id} not found`);
    return task;
  }

  /**
   * GET /api/lam/tasks — recent tasks for the history list.
   */
  @Get('tasks')
  async listTasks(
    @CurrentUser() user: AuthUser,
    @Query('limit') limit?: string,
  ): Promise<LamTask[]> {
    const n = limit ? Number.parseInt(limit, 10) : 20;
    return this.orchestrator.listTasks(user.id, Number.isFinite(n) ? n : 20);
  }

  /**
   * GET /api/lam/adapters — which adapters are available.
   * Browser is always available; Computer-Use requires the flag.
   */
  @Get('adapters')
  async adapters(): Promise<Array<{ id: LamAdapterId; available: boolean; reason?: string; premium?: boolean }>> {
    return this.orchestrator.adapterStatus();
  }

  // -------------------------------------------------------------------------
  // Tier gate helper
  // -------------------------------------------------------------------------

  /**
   * Throw 403 if the user is not a VERIFIED_BUILDER (Doxxed). Used to
   * gate Computer-Use and any future premium adapter. The message is
   * the spec'd one so the frontend can render it verbatim.
   */
  private async requireDoxxedTier(userId: string): Promise<void> {
    const u = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { builderTier: true },
    });
    if (u?.builderTier !== 'VERIFIED_BUILDER') {
      throw new ForbiddenException(COMPUTER_USE_TIER_MESSAGE);
    }
  }
}

/**
 * DTO for POST /api/lam/task. Kept inline (no class-validator decorators)
 * because the global ValidationPipe isn't guaranteed and the controller
 * does explicit length checks. Matches the Idea Validator's DTO discipline.
 */
export interface SubmitLamTaskDto {
  goal: string;
}
