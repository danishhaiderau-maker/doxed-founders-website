import { Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AiProxyRuntimeService, type ProxyAuth } from '../ai-proxy/ai-proxy-runtime.service';
import { FOUNDER_OS_AUTO_MODEL } from '../ai-proxy/ai-proxy.constants';
import type { ChatCompletionRequestDto } from '../ai-proxy/dto/ai-proxy.dto';
import { FlightRecorderService } from '../flight-recorder/flight-recorder.service';
import { BrowserAdapter } from './browser.adapter';
import { ComputerUseAdapter } from './computer-use.adapter';
import type {
  LamAdapterId,
  LamPlan,
  LamStep,
  LamStepResult,
  LamTask,
} from './lam.types';

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
 * Task state is held in-memory (Map<taskId, LamTask>). Phase 9 ships
 * the slice; a later phase can persist to a Prisma table if the UI
 * needs cross-restart history. The controller exposes the same shape
 * either way.
 */
@Injectable()
export class LamOrchestratorService {
  private readonly logger = new Logger(LamOrchestratorService.name);
  private readonly tasks = new Map<string, LamTask>();
  private static readonly MAX_STEPS = 12;
  private static readonly MAX_TASK_MS = 180_000;

  constructor(
    private readonly aiProxy: AiProxyRuntimeService,
    private readonly flightRecorder: FlightRecorderService,
    private readonly browser: BrowserAdapter,
    private readonly computerUse: ComputerUseAdapter,
  ) {}

  /**
   * Submit a natural-language task. Returns the taskId immediately;
   * the actual planning + execution runs async so the caller can poll
   * GET /api/lam/task/:id for progress.
   */
  async submitTask(auth: ProxyAuth, goal: string): Promise<LamTask> {
    const now = new Date().toISOString();
    const task: LamTask = {
      id: randomUUID(),
      userId: auth.userId,
      goal,
      status: 'PLANNING',
      steps: [],
      results: [],
      createdAt: now,
      updatedAt: now,
    };
    this.tasks.set(task.id, task);

    // Fire-and-forget the async run. Errors land on the task record as
    // status FAILED so the client always sees a terminal state.
    void this.runTask(auth, task).catch((err) => {
      this.logger.error(`LAM task ${task.id} crashed: ${err instanceof Error ? err.message : String(err)}`);
      task.status = 'FAILED';
      task.error = err instanceof Error ? err.message : String(err);
      task.updatedAt = new Date().toISOString();
    });

    return task;
  }

  /** Fetch a task by id, scoped to the caller's user id. */
  getTask(userId: string, taskId: string): LamTask | null {
    const t = this.tasks.get(taskId);
    if (!t || t.userId !== userId) return null;
    return t;
  }

  /** Recent tasks for the history list (most recent first). */
  listTasks(userId: string, limit = 20): LamTask[] {
    return Array.from(this.tasks.values())
      .filter((t) => t.userId === userId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .slice(0, limit);
  }

  // -------------------------------------------------------------------------
  // Planning + execution
  // -------------------------------------------------------------------------

  private async runTask(auth: ProxyAuth, task: LamTask): Promise<void> {
    const startedAt = Date.now();

    // 1. PLAN
    const plan = await this.planGoal(auth, task.goal);
    task.steps = plan.steps.slice(0, LamOrchestratorService.MAX_STEPS).map((s, i) => ({
      index: i + 1,
      description: s.description,
      adapter: s.adapter,
      payload: s.payload,
    }));
    task.status = 'RUNNING';
    task.updatedAt = new Date().toISOString();
    await this.logLamDecision(auth, 'plan', `planned ${task.steps.length} steps for goal`, task.goal);

    // 2. EXECUTE
    const deadline = startedAt + LamOrchestratorService.MAX_TASK_MS;
    for (const step of task.steps) {
      if (Date.now() > deadline) {
        task.results.push({
          index: step.index,
          status: 'skipped',
          summary: 'skipped — task deadline exceeded',
          startedAt: new Date().toISOString(),
          completedAt: new Date().toISOString(),
        });
        break;
      }
      const result = await this.executeStep(auth, step);
      task.results.push(result);
      task.updatedAt = new Date().toISOString();
    }

    // 3. SYNTHESIZE
    const successes = task.results.filter((r) => r.status === 'success').length;
    const majorityOk = successes >= Math.ceil(task.steps.length / 2);
    if (task.steps.length === 0 || !majorityOk) {
      task.status = 'FAILED';
      task.error = `only ${successes}/${task.steps.length} steps succeeded`;
    } else {
      try {
        task.result = await this.synthesize(auth, task);
        task.status = 'COMPLETED';
      } catch (err) {
        // Synthesis failing is non-fatal — we still have the step results.
        task.result = `Steps completed (${successes}/${task.steps.length} succeeded) but synthesis failed: ${err instanceof Error ? err.message : String(err)}`;
        task.status = 'COMPLETED';
      }
    }
    task.elapsedMs = Date.now() - startedAt;
    task.costDdollar = task.steps.length; // rough: ~1 DDollar per step (DDollar is metered by the AI Gateway per model call)
    task.updatedAt = new Date().toISOString();
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

  adapterStatus(): Array<{ id: LamAdapterId; available: boolean; reason?: string; premium?: boolean }> {
    return [
      { id: 'browser', available: this.browser.isConnected() },
      {
        id: 'computer-use',
        available: this.computerUse.isEnabled(),
        premium: true,
        reason: this.computerUse.isEnabled()
          ? undefined
          : 'Premium tier not enabled (COMPUTER_USE_ENABLED != true). Doxxed Builders only.',
      },
    ];
  }
}
