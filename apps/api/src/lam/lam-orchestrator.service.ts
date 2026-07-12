import { Injectable, Logger } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { randomUUID } from 'node:crypto';
import { AiProxyRuntimeService, type ProxyAuth } from '../ai-proxy/ai-proxy-runtime.service';
import { FOUNDER_OS_AUTO_MODEL } from '../ai-proxy/ai-proxy.constants';
import type { ChatCompletionRequestDto } from '../ai-proxy/dto/ai-proxy.dto';
import { FlightRecorderService } from '../flight-recorder/flight-recorder.service';
import { PrismaService } from '../prisma/prisma.service';
import { BrowserAdapter } from './browser.adapter';
import { ComputerUseAdapter } from './computer-use.adapter';
import type {
  LamAdapterId,
  LamPlan,
  LamStep,
  LamStepResult,
  LamTask,
} from './lam.types';

const json = <T>(value: T): Prisma.InputJsonValue => value as unknown as Prisma.InputJsonValue;

/**
 * LamOrchestratorService — the "hands" to the AI Gateway's "brain."
 *
 * Flow:
 *   1. PLANNING  — hand the founder's natural-language goal to the AI
 *                  Gateway, ask it to return a strict-JSON LamPlan
 *                  (ordered steps, each tagged with adapter + payload).
 *   2. RUNNING   — execute each step via the right adapter, recording
 *                  a LamStepResult per step. Step failures are captured
 *                  but don't abort the whole task (later steps may
 *                  still be runnable); the orchestrator marks the task
 *                  COMPLETED if a majority succeed, else FAILED.
 *   3. SYNTHESIS — once steps finish, ask the AI Gateway to synthesize
 *                  the final answer from the step results + the goal.
 *
 * Every model call goes through AiProxyRuntimeService so DDollar is
 * metered and the Flight Recorder captures the plan + synthesis as
 * routing decisions (intent 'lam'). Every adapter action is also
 * logged to the Flight Recorder so the full action trace is
 * reconstructable — this is the training data for the Learning Engine.
 *
 * Task + step state is persisted to Prisma (LamTask / LamStep) so it
 * survives restarts and the controller can read history from the DB.
 * The controller exposes the same LamTask shape either way.
 */
@Injectable()
export class LamOrchestratorService {
  private readonly logger = new Logger(LamOrchestratorService.name);
  private static readonly MAX_STEPS = 12;
  private static readonly MAX_TASK_MS = 180_000;
  private static readonly MAX_ATTEMPTS = 3;
  private static readonly STALE_CLAIM_MS = 5 * 60_000;

  constructor(
    private readonly aiProxy: AiProxyRuntimeService,
    private readonly flightRecorder: FlightRecorderService,
    private readonly browser: BrowserAdapter,
    private readonly computerUse: ComputerUseAdapter,
    private readonly prisma: PrismaService,
  ) {}

  /**
   * Submit a natural-language task. Persists a LamTask row in PLANNING state
   * and returns the mapped task immediately; the actual planning + execution
   * runs async so the caller can poll GET /api/lam/task/:id for progress.
   */
  async submitTask(auth: ProxyAuth, goal: string): Promise<LamTask> {
    const existing = await this.prisma.lamTask.findFirst({
      where: {
        userId: auth.userId,
        goal,
        status: { in: ['PLANNING', 'RUNNING', 'AWAITING_CONFIRMATION'] },
        createdAt: { gte: new Date(Date.now() - 5 * 60_000) },
      },
      orderBy: { createdAt: 'desc' },
    });
    if (existing) return this.rowToTask(existing, []);

    const row = await this.prisma.lamTask.create({
      data: {
        id: randomUUID(),
        userId: auth.userId,
        goal,
        status: 'PLANNING',
        planJson: json([]),
        resultJson: json([]),
      },
    });

    // This is only an eager wake-up. The durable scheduler will retry the
    // persisted row after a process restart or transient failure.
    void this.processQueuedTasks(1).catch((err) =>
      this.logger.error(`LAM eager worker failed: ${err instanceof Error ? err.message : String(err)}`),
    );

    return this.rowToTask(row, []);
  }

  /** Durable worker entry point, called by LamScheduler and submitTask. */
  async processQueuedTasks(limit = 5): Promise<number> {
    await this.requeueStaleClaims();
    const candidates = await this.prisma.lamTask.findMany({
      where: {
        status: 'PLANNING',
        OR: [{ nextAttemptAt: null }, { nextAttemptAt: { lte: new Date() } }],
      },
      orderBy: { createdAt: 'asc' },
      take: Math.max(1, Math.min(limit, 20)),
    });
    let processed = 0;
    for (const candidate of candidates) {
      const claim = await this.prisma.lamTask.updateMany({
        where: { id: candidate.id, status: 'PLANNING' },
        data: {
          status: 'RUNNING',
          executionClaimedAt: new Date(),
          nextAttemptAt: null,
          attemptCount: { increment: 1 },
          errorMessage: null,
        },
      });
      if (claim.count !== 1) continue;
      processed += 1;
      try {
        await this.runTask({ userId: candidate.userId, nodeId: 'lam-worker' }, candidate.id, candidate.goal);
      } catch (err) {
        await this.scheduleRetry(candidate.id, err instanceof Error ? err : new Error(String(err)));
      }
    }
    return processed;
  }

  /** Confirm the next externally-effecting step and return it to the queue. */
  async confirmTask(userId: string, taskId: string): Promise<LamTask | null> {
    const row = await this.prisma.lamTask.findFirst({
      where: { id: taskId, userId, status: 'AWAITING_CONFIRMATION' },
      include: { steps: { orderBy: { stepIndex: 'asc' } } },
    });
    if (!row || !row.confirmationStepIndex) return null;
    const updated = await this.prisma.lamTask.update({
      where: { id: row.id },
      data: {
        status: 'PLANNING',
        confirmedStepIndex: row.confirmationStepIndex,
        confirmedAt: new Date(),
        confirmationStepIndex: null,
        nextAttemptAt: new Date(),
      },
      include: { steps: { orderBy: { stepIndex: 'asc' } } },
    });
    void this.processQueuedTasks(1).catch(() => {});
    return this.rowToTask(updated, updated.steps);
  }

  /** Fetch a task by id, scoped to the caller's user id. */
  async getTask(userId: string, taskId: string): Promise<LamTask | null> {
    const row = await this.prisma.lamTask.findUnique({
      where: { id: taskId },
      include: { steps: { orderBy: { stepIndex: 'asc' } } },
    });
    if (!row || row.userId !== userId) return null;
    return this.rowToTask(row, row.steps);
  }

  /** Recent tasks for the history list (most recent first). */
  async listTasks(userId: string, limit = 20): Promise<LamTask[]> {
    const rows = await this.prisma.lamTask.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      take: Math.max(1, Math.min(limit, 100)),
    });
    return rows.map((r) => this.rowToTask(r, []));
  }

  // -------------------------------------------------------------------------
  // Planning + execution
  // -------------------------------------------------------------------------

  private async runTask(auth: ProxyAuth, taskId: string, goal: string): Promise<void> {
    const startedAt = Date.now();
    const stored = await this.prisma.lamTask.findUnique({
      where: { id: taskId },
      include: { steps: { orderBy: { stepIndex: 'asc' } } },
    });
    if (!stored) throw new Error(`LAM task ${taskId} disappeared before execution`);

    // A persisted plan is resumed exactly as recorded; the model is not asked
    // to produce a different plan after a worker restart.
    let steps = this.stepsFromJson(stored.planJson);
    if (steps.length === 0) {
      const plan = await this.planGoal(auth, goal);
      steps = plan.steps.slice(0, LamOrchestratorService.MAX_STEPS).map((s, i) => ({
        index: i + 1,
        description: s.description,
        adapter: s.adapter,
        payload: s.payload,
      }));
      await this.prisma.lamTask.update({
        where: { id: taskId },
        data: { status: 'RUNNING', planJson: json(steps), executionClaimedAt: new Date() },
      });
      await this.logLamDecision(auth, 'plan', `planned ${steps.length} steps for goal`, goal);
    } else {
      await this.prisma.lamTask.update({
        where: { id: taskId },
        data: { status: 'RUNNING', executionClaimedAt: new Date() },
      });
    }

    const knownSteps = new Set(stored.steps.map((step) => step.stepIndex));
    const missingStepRows = steps.filter((step) => !knownSteps.has(step.index));
    if (missingStepRows.length > 0) {
      await this.prisma.lamStep.createMany({
        data: missingStepRows.map((step) => ({
          taskId,
          stepIndex: step.index,
          action: step.description,
          adapter: step.adapter,
          inputJson: json(step.payload ?? {}),
          status: 'pending',
        })),
      });
    }

    // 2. EXECUTE
    const deadline = startedAt + LamOrchestratorService.MAX_TASK_MS;
    const priorResults = this.resultsFromJson(stored.resultJson, stored.createdAt, stored.updatedAt);
    const results: LamStepResult[] = [];
    for (const step of steps) {
      const prior = priorResults.get(step.index);
      if (prior) {
        results.push(prior);
        continue;
      }
      if (Date.now() > deadline) {
        results.push({
          index: step.index,
          status: 'skipped',
          summary: 'skipped — task deadline exceeded',
          startedAt: new Date().toISOString(),
          completedAt: new Date().toISOString(),
        });
        await this.updateStepRow(taskId, step.index, 'skipped', undefined, undefined, 0);
        continue;
      }
      if (this.requiresConfirmation(step) && stored.confirmedStepIndex !== step.index) {
        await this.prisma.lamTask.update({
          where: { id: taskId },
          data: {
            status: 'AWAITING_CONFIRMATION',
            confirmationStepIndex: step.index,
            executionClaimedAt: null,
            resultJson: json(results),
          },
        });
        return;
      }
      await this.updateStepRow(taskId, step.index, 'running', undefined, undefined, 0);
      const result = await this.executeStep(auth, step);
      results.push(result);
      await this.prisma.lamTask.update({
        where: { id: taskId },
        data: { resultJson: json(results) },
      });
      await this.updateStepRow(
        taskId,
        step.index,
        result.status,
        { summary: result.summary, artifacts: result.artifacts },
        result.error,
        Date.now() - new Date(result.startedAt).getTime(),
      );
    }

    // 3. SYNTHESIZE
    await this.prisma.lamTask.update({
      where: { id: taskId },
      data: { status: 'SYNTHESIZING', executionClaimedAt: new Date() },
    });
    const successes = results.filter((r) => r.status === 'success').length;
    const majorityOk = successes >= Math.ceil(steps.length / 2);
    let status: 'COMPLETED' | 'FAILED';
    let resultText: string;
    let errorMessage: string | null = null;

    if (steps.length === 0 || !majorityOk) {
      status = 'FAILED';
      errorMessage = `only ${successes}/${steps.length} steps succeeded`;
      resultText = errorMessage;
    } else {
      try {
        // Load the in-progress task for synthesis (needs goal + results).
        const task = this.synthTask(goal, steps, results);
        resultText = await this.synthesize(auth, task);
        status = 'COMPLETED';
      } catch (err) {
        resultText = `Steps completed (${successes}/${steps.length} succeeded) but synthesis failed: ${err instanceof Error ? err.message : String(err)}`;
        status = 'COMPLETED';
      }
    }

    await this.prisma.lamTask.update({
      where: { id: taskId },
      data: {
        status,
        result: resultText,
        errorMessage,
        resultJson: json(results),
        elapsedMs: Date.now() - startedAt,
        costDdollar: steps.length,
        completedAt: new Date(),
        executionClaimedAt: null,
        nextAttemptAt: null,
      },
    });
  }

  private stepsFromJson(value: Prisma.JsonValue): LamStep[] {
    if (!Array.isArray(value)) return [];
    return value.flatMap((raw, index) => {
      const step = (raw ?? {}) as {
        index?: unknown;
        description?: unknown;
        adapter?: unknown;
        payload?: unknown;
      };
      if (typeof step.description !== 'string') return [];
      return [{
        index: typeof step.index === 'number' ? step.index : index + 1,
        description: step.description,
        adapter: step.adapter === 'computer-use' ? 'computer-use' as const : 'browser' as const,
        payload: step.payload ?? {},
      }];
    });
  }

  private resultsFromJson(
    value: Prisma.JsonValue,
    createdAt: Date,
    updatedAt: Date,
  ): Map<number, LamStepResult> {
    const entries = Array.isArray(value) ? value : [];
    const results = new Map<number, LamStepResult>();
    for (const [index, raw] of entries.entries()) {
      const result = (raw ?? {}) as Partial<LamStepResult>;
      if (!['success', 'failed', 'skipped'].includes(result.status ?? '')) continue;
      results.set(typeof result.index === 'number' ? result.index : index + 1, {
        index: typeof result.index === 'number' ? result.index : index + 1,
        status: result.status as LamStepResult['status'],
        summary: result.summary ?? '',
        artifacts: result.artifacts,
        error: result.error,
        startedAt: result.startedAt ?? createdAt.toISOString(),
        completedAt: result.completedAt ?? updatedAt.toISOString(),
      });
    }
    return results;
  }

  private requiresConfirmation(step: LamStep): boolean {
    if (step.adapter === 'computer-use') return true;
    const action = (step.payload as { action?: unknown })?.action;
    return action === 'click' || action === 'fillForm';
  }

  /** Build a minimal LamTask shape for the synthesizer (in-memory only). */
  private synthTask(goal: string, steps: LamStep[], results: LamStepResult[]): LamTask {
    return {
      id: 'synthesis',
      userId: '',
      goal,
      status: 'SYNTHESIZING',
      steps,
      results,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
  }

  /** Update a LamStep row with the outcome of executing it. */
  private async updateStepRow(
    taskId: string,
    stepIndex: number,
    status: string,
    output: { summary: string; artifacts?: string[] } | undefined,
    error: string | undefined,
    durationMs: number,
  ): Promise<void> {
    try {
      await this.prisma.lamStep.updateMany({
        where: { taskId, stepIndex },
        data: {
          status,
          outputJson: output ? json(output) : Prisma.JsonNull,
          error: error ?? null,
          durationMs,
        },
      });
    } catch (err) {
      this.logger.debug(
        `LamStep update failed for task=${taskId} step=${stepIndex}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /** A crashed worker loses its lease, never the task or completed steps. */
  private async requeueStaleClaims(): Promise<void> {
    const staleBefore = new Date(Date.now() - LamOrchestratorService.STALE_CLAIM_MS);
    await this.prisma.lamTask.updateMany({
      where: {
        status: { in: ['RUNNING', 'SYNTHESIZING'] },
        OR: [
          { executionClaimedAt: { lt: staleBefore } },
          { executionClaimedAt: null },
        ],
      },
      data: {
        status: 'PLANNING',
        executionClaimedAt: null,
        nextAttemptAt: new Date(),
        errorMessage: 'Recovered after a worker lease expired; resuming persisted plan.',
      },
    });
  }

  private async scheduleRetry(taskId: string, error: Error): Promise<void> {
    const row = await this.prisma.lamTask.findUnique({ where: { id: taskId } });
    if (!row) return;
    const terminal = row.attemptCount >= LamOrchestratorService.MAX_ATTEMPTS;
    const delayMs = Math.min(2 ** Math.min(row.attemptCount, 10) * 60_000, 60 * 60_000);
    await this.prisma.lamTask.update({
      where: { id: taskId },
      data: terminal
        ? {
            status: 'FAILED',
            errorMessage: error.message.slice(0, 2_000),
            executionClaimedAt: null,
            completedAt: new Date(),
          }
        : {
            status: 'PLANNING',
            errorMessage: error.message.slice(0, 2_000),
            executionClaimedAt: null,
            nextAttemptAt: new Date(Date.now() + delayMs),
          },
    });
  }

  /**
   * Ask the AI Gateway to plan the goal. Returns a strict-JSON LamPlan.
   * Falls back to a single navigate+extract step if the model call
   * fails so the orchestrator always has something to execute.
   */
  private async planGoal(auth: ProxyAuth, goal: string): Promise<LamPlan> {
    const systemPrompt =
      'You are the Founder OS LAM planner. Break the founder\'s goal into discrete browser/desktop steps. ' +
      'Return STRICT JSON: {"steps":[{"description":"...","adapter":"browser"|"computer-use","payload":{...}}]}. ' +
      'Browser payloads use action navigate|extract|click|fillForm|screenshot with url/selector/fields. ' +
      'Prefer the fewest steps that complete the goal. For research-style goals, use navigate to a search ' +
      'engine then extract. Do not include computer-use steps unless the goal explicitly requires desktop control.';

    const userPrompt =
      `FOUNDER GOAL: ${goal}\n\n` +
      `Return a JSON plan. Each step needs description, adapter, and a payload object. ` +
      `JSON only, no prose.`;

    try {
      const body: ChatCompletionRequestDto = {
        model: FOUNDER_OS_AUTO_MODEL,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        max_tokens: 800,
        temperature: 0,
        response_format: { type: 'json_object' },
      };
      const route = await this.aiProxy.decideRoute(auth, body);
      const result = await this.aiProxy.invoke(auth, body, route);
      if (!result.ok) {
        return this.fallbackPlan(goal, `planner model call failed (${result.status})`);
      }
      const parsed = JSON.parse(typeof result.body === 'string' ? result.body : '') as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      const content = parsed.choices?.[0]?.message?.content ?? '';
      const plan = this.parsePlan(content);
      return plan ?? this.fallbackPlan(goal, 'unparseable planner output');
    } catch (err) {
      this.logger.warn(
        `planGoal failed: ${err instanceof Error ? err.message : String(err)} — using fallback plan`,
      );
      return this.fallbackPlan(goal, err instanceof Error ? err.message : String(err));
    }
  }

  /**
   * Ask the AI Gateway to synthesize a final answer from the step results.
   */
  private async synthesize(auth: ProxyAuth, task: LamTask): Promise<string> {
    const systemPrompt =
      'You are the Founder OS LAM synthesizer. Given the founder\'s goal and the step results, ' +
      'produce a concise, useful answer (2-4 sentences). Be specific. Cite URLs from the artifacts when relevant.';
    const stepDigest = task.results
      .map((r) => `Step ${r.index} [${r.status}]: ${r.summary}${r.artifacts && r.artifacts.length ? ` (${r.artifacts.join(', ')})` : ''}`)
      .join('\n');
    const userPrompt = `GOAL: ${task.goal}\n\nSTEP RESULTS:\n${stepDigest}\n\nAnswer the founder.`;

    const body: ChatCompletionRequestDto = {
      model: FOUNDER_OS_AUTO_MODEL,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      max_tokens: 500,
      temperature: 0.2,
    };
    const route = await this.aiProxy.decideRoute(auth, body);
    const result = await this.aiProxy.invoke(auth, body, route);
    if (!result.ok) {
      throw new Error(`synthesis model call failed (${result.status})`);
    }
    const parsed = JSON.parse(typeof result.body === 'string' ? result.body : '') as {
      choices?: Array<{ message?: { content?: string } }>;
    };
    const content = parsed.choices?.[0]?.message?.content ?? '';
    if (!content.trim()) throw new Error('synthesis returned empty content');
    return content.trim();
  }

  /**
   * Defensive parse of the planner's JSON. Mirrors the BrowserResearchAdapter
   * discipline: tolerate prose around the JSON, only accept fields we know.
   */
  private parsePlan(content: string): LamPlan | null {
    try {
      const start = content.indexOf('{');
      const end = content.lastIndexOf('}');
      if (start === -1 || end === -1) return null;
      const obj = JSON.parse(content.slice(start, end + 1)) as { steps?: unknown };
      if (!Array.isArray(obj.steps)) return null;
      const steps: LamPlan['steps'] = [];
      for (const raw of obj.steps) {
        if (!raw || typeof raw !== 'object') continue;
        const s = raw as { description?: unknown; adapter?: unknown; payload?: unknown };
        if (typeof s.description !== 'string') continue;
        const adapter = s.adapter === 'computer-use' ? 'computer-use' : 'browser';
        steps.push({
          description: s.description,
          adapter: adapter as LamAdapterId,
          payload: s.payload && typeof s.payload === 'object' ? s.payload : {},
        });
      }
      return steps.length > 0 ? { steps } : null;
    } catch {
      return null;
    }
  }

  /**
   * Last-resort plan when the planner model is unavailable: a single
   * navigate + extract against a DuckDuckGo search. Keeps the LAM
   * functional during model outages.
   */
  private fallbackPlan(goal: string, reason: string): LamPlan {
    this.logger.warn(`LAM using fallback plan for goal "${goal.slice(0, 60)}" (${reason})`);
    const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(goal.slice(0, 200))}`;
    return {
      steps: [
        {
          description: 'Search the web for the goal (fallback — planner unavailable)',
          adapter: 'browser',
          payload: { action: 'navigate', url },
        },
        {
          description: 'Extract the search results',
          adapter: 'browser',
          payload: { action: 'extract', url },
        },
      ],
    };
  }

  /**
   * Execute one step via the right adapter. Catches all errors so a
   * single step failure never aborts the task — the result is marked
   * failed and the loop continues.
   */
  private async executeStep(auth: ProxyAuth, step: LamStep): Promise<LamStepResult> {
    const startedAt = new Date().toISOString();
    try {
      let outcome: { summary: string; artifacts?: string[] };
      if (step.adapter === 'computer-use') {
        outcome = await this.computerUse.runStep(step.payload as never);
      } else {
        outcome = await this.browser.runStep(step.payload as never);
      }
      await this.logLamDecision(
        auth,
        step.adapter,
        `step ${step.index}: ${step.description.slice(0, 80)}`,
        outcome.summary,
      );
      return {
        index: step.index,
        status: 'success',
        summary: outcome.summary,
        artifacts: outcome.artifacts,
        startedAt,
        completedAt: new Date().toISOString(),
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.logger.warn(`LAM step ${step.index} failed: ${message}`);
      await this.logLamDecision(
        auth,
        step.adapter,
        `step ${step.index} FAILED: ${step.description.slice(0, 80)}`,
        message,
      );
      return {
        index: step.index,
        status: 'failed',
        summary: step.description,
        error: message,
        startedAt,
        completedAt: new Date().toISOString(),
      };
    }
  }

  /**
   * Log every LAM decision (plan + per-step) to the Flight Recorder as a
   * RoutingDecision-style row. intent 'lam', chosenProvider 'local-lam' so
   * it's distinguishable from LLM model calls and from the Phase 6
   * 'research' intent. This is the LAM action trace.
   */
  private async logLamDecision(
    auth: ProxyAuth,
    adapter: string,
    detail: string,
    payloadDigest: string,
  ): Promise<void> {
    try {
      await this.flightRecorder.record({
        requestId: randomUUID(),
        userId: auth.userId,
        workspaceId: null,
        intent: 'lam',
        profile: 'autonomous',
        candidates: [],
        chosenProvider: 'local-lam',
        chosenModel: `lam:${adapter}`,
        cacheLevel: 'miss',
        cacheKey: null,
        promptHash: `${adapter}:${detail.slice(0, 64)}`,
        tokenCountPrompt: 0,
        tokenCountCompletion: 0,
        latencyMs: 0,
        costUsd: 0,
      });
      void payloadDigest;
    } catch (err) {
      this.logger.debug(
        `flight recorder write for LAM decision failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  // -------------------------------------------------------------------------
  // Adapter availability — used by the controller + frontend
  // -------------------------------------------------------------------------

  adapterStatus(): Array<{
    id: LamAdapterId;
    available: boolean;
    reason?: string;
    premium?: boolean;
    contract?: ReturnType<ComputerUseAdapter['describeContract']>;
  }> {
    return [
      { id: 'browser', available: this.browser.isConnected() },
      {
        id: 'computer-use',
        available: this.computerUse.isEnabled(),
        premium: true,
        reason: this.computerUse.isEnabled()
          ? undefined
          : 'Premium tier not enabled (COMPUTER_USE_ENABLED != true). Doxxed Builders only.',
        contract: this.computerUse.describeContract(),
      },
    ];
  }

  // -------------------------------------------------------------------------
  // Prisma → interface mapping
  // -------------------------------------------------------------------------

  /** Map a LamTask Prisma row (with optional steps) to the LamTask interface. */
  private rowToTask(
    row: {
      id: string;
      userId: string;
      goal: string;
      status: string;
      planJson: Prisma.JsonValue;
      resultJson: Prisma.JsonValue;
      result: string | null;
      elapsedMs: number | null;
      costDdollar: number | null;
      errorMessage: string | null;
      createdAt: Date;
      updatedAt: Date;
      completedAt: Date | null;
      confirmationStepIndex?: number | null;
    },
    stepRows: Array<{
      stepIndex: number;
      action: string;
      adapter: string;
      inputJson: Prisma.JsonValue;
      status: string;
    }>,
  ): LamTask {
    const steps: LamStep[] = Array.isArray(row.planJson)
      ? (row.planJson as unknown[]).map((raw, i) => {
          const s = (raw ?? {}) as { index?: number; description?: string; adapter?: string; payload?: unknown };
          return {
            index: s.index ?? i + 1,
            description: s.description ?? stepRows[i]?.action ?? '',
            adapter: (s.adapter === 'computer-use' ? 'computer-use' : 'browser') as LamAdapterId,
            payload: s.payload ?? stepRows[i]?.inputJson ?? {},
          };
        })
      : stepRows.map((r) => ({
          index: r.stepIndex,
          description: r.action,
          adapter: (r.adapter === 'computer-use' ? 'computer-use' : 'browser') as LamAdapterId,
          payload: r.inputJson ?? {},
        }));

    const results: LamStepResult[] = Array.isArray(row.resultJson)
      ? (row.resultJson as unknown[]).map((raw, i) => {
          const r = (raw ?? {}) as {
            index?: number;
            status?: string;
            summary?: string;
            artifacts?: string[];
            error?: string;
            startedAt?: string;
            completedAt?: string;
          };
          return {
            index: r.index ?? i + 1,
            status: (r.status === 'success' || r.status === 'failed' || r.status === 'skipped'
              ? r.status
              : 'skipped') as LamStepResult['status'],
            summary: r.summary ?? '',
            artifacts: r.artifacts,
            error: r.error,
            startedAt: r.startedAt ?? row.createdAt.toISOString(),
            completedAt: r.completedAt ?? row.updatedAt.toISOString(),
          };
        })
      : [];

    return {
      id: row.id,
      userId: row.userId,
      goal: row.goal,
      status: this.normalizeStatus(row.status),
      steps,
      results,
      result: row.result ?? undefined,
      elapsedMs: row.elapsedMs ?? undefined,
      costDdollar: row.costDdollar ?? undefined,
      error: row.errorMessage ?? undefined,
      confirmationStepIndex: row.confirmationStepIndex ?? undefined,
      createdAt: row.createdAt.toISOString(),
      updatedAt: row.updatedAt.toISOString(),
    };
  }

  private normalizeStatus(s: string): LamTask['status'] {
    if (s === 'RUNNING' || s === 'PLANNING' || s === 'AWAITING_CONFIRMATION' || s === 'SYNTHESIZING' || s === 'COMPLETED' || s === 'FAILED') {
      return s;
    }
    return 'PLANNING';
  }
}
