import { Injectable, Logger, Optional } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AiProxyRuntimeService } from '../ai-proxy/ai-proxy-runtime.service';
import { FlightRecorderService } from '../flight-recorder/flight-recorder.service';
import { ExecutionManagerService } from '../execution-manager/execution-manager.service';
import type {
  DecomposeGoalInput,
  IntentDecomposition,
  IntentStep,
  IntentStepExecution,
} from './intent-engine.types';

const DECOMPOSE_SYSTEM = `You are the Founder OS Intent Engine. Decompose the founder's goal into an ordered list of concrete execution steps.
Return STRICT JSON only (no markdown):
{"steps":[{"title":"...","description":"...","suggestedTarget":"terminal"|"filesystem"|"browser"|"cursor"|"vscode"|null,"actionHint":"file-read"|"list-workspace"|null}]}
Keep 3–8 steps. Prefer actionable engineering steps. suggestedTarget is optional.
Put a safe inspect step first when possible (filesystem list/read) before terminal or browser.`;

/**
 * Intent Engine — kernel service #10.
 *
 * Input:  goal string (+ optional projectId, executeFirstStep)
 * Decision: ask AI Gateway for a step list; optionally run first safe step
 * Output: IntentDecomposition + Flight Recorder row (+ optional execution)
 *
 * Failures degrade to a deterministic 3-step fallback so callers always
 * get a graph shape they can render / hand to Execution Manager.
 */
@Injectable()
export class IntentEngineService {
  private readonly logger = new Logger(IntentEngineService.name);

  constructor(
    private readonly aiProxy: AiProxyRuntimeService,
    private readonly flightRecorder: FlightRecorderService,
    @Optional() private readonly executionManager?: ExecutionManagerService,
  ) {}

  async decomposeGoal(input: DecomposeGoalInput): Promise<IntentDecomposition> {
    const requestId = randomUUID();
    const goalId = randomUUID();
    const goal = (input.goal ?? '').trim();
    const maxSteps = Math.max(2, Math.min(input.maxSteps ?? 8, 12));

    if (!goal) {
      return {
        goalId,
        goal: '',
        steps: [],
        requestId,
        createdAt: new Date().toISOString(),
      };
    }

    let steps: IntentStep[] = [];
    let provider: string | undefined;
    let model: string | undefined;

    try {
      const route = await this.aiProxy.decideRoute(
        { userId: input.userId, nodeId: 'intent-engine' },
        {
          model: 'founder-os-auto',
          messages: [
            { role: 'system', content: DECOMPOSE_SYSTEM },
            {
              role: 'user',
              content: `Goal: ${goal}\nMax steps: ${maxSteps}${
                input.projectId ? `\nProject: ${input.projectId}` : ''
              }`,
            },
          ],
          stream: false,
          max_tokens: 1200,
          temperature: 0.2,
          response_format: { type: 'json_object' },
        },
      );
      provider = route.providerKey;
      model = route.model;

      const result = await this.aiProxy.invoke(
        { userId: input.userId, nodeId: 'intent-engine' },
        {
          model: 'founder-os-auto',
          messages: [
            { role: 'system', content: DECOMPOSE_SYSTEM },
            {
              role: 'user',
              content: `Goal: ${goal}\nMax steps: ${maxSteps}${
                input.projectId ? `\nProject: ${input.projectId}` : ''
              }`,
            },
          ],
          stream: false,
          max_tokens: 1200,
          temperature: 0.2,
          response_format: { type: 'json_object' },
        },
        route,
      );

      if (result.ok && typeof result.body === 'string') {
        steps = this.parseSteps(result.body, maxSteps);
      }
    } catch (err) {
      this.logger.warn(
        `Intent Engine AI decompose failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    if (steps.length === 0) {
      steps = this.fallbackSteps(goal);
    }

    let firstStepExecution: IntentStepExecution | undefined;
    if (input.executeFirstStep) {
      firstStepExecution = await this.executeFirstSafeStep(steps[0], input.cwd);
    }

    // Flight Recorder — log the decomposition as an agent-intent decision.
    try {
      await this.flightRecorder.record({
        requestId,
        userId: input.userId,
        workspaceId: input.projectId ?? null,
        intent: 'agent',
        profile: 'architect',
        candidates: [],
        chosenProvider: provider ?? 'intent-engine',
        chosenModel: model ?? 'fallback-heuristic',
        cacheLevel: 'miss',
        promptHash: `intent:${goalId}`,
      });
    } catch (err) {
      this.logger.debug(
        `Flight Recorder write failed for intent decompose: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }

    return {
      goalId,
      goal,
      steps,
      provider,
      model,
      requestId,
      createdAt: new Date().toISOString(),
      firstStepExecution,
    };
  }

  /**
   * Only filesystem list is allowed. Shell, write, git, browser → unsafe skip.
   */
  private async executeFirstSafeStep(
    step: IntentStep | undefined,
    cwd?: string,
  ): Promise<IntentStepExecution> {
    if (!step) {
      return {
        stepId: 'none',
        attempted: false,
        status: 'skipped',
        detail: 'No steps to execute',
      };
    }

    if (!this.executionManager) {
      return {
        stepId: step.id,
        attempted: false,
        status: 'skipped',
        detail: 'Execution Manager not available in this process',
      };
    }

    const target = step.suggestedTarget;
    if (target && target !== 'filesystem') {
      return {
        stepId: step.id,
        attempted: false,
        status: 'unsafe',
        detail: `Refused auto-execute suggestedTarget="${target}" — only filesystem list is allowed for executeFirstStep`,
      };
    }

    const allowWithoutTarget =
      !target &&
      /inspect|list|read|workspace|files?/i.test(`${step.title} ${step.description}`);

    if (target !== 'filesystem' && !allowWithoutTarget) {
      return {
        stepId: step.id,
        attempted: false,
        status: 'skipped',
        detail: 'First step is not a safe filesystem inspect action',
      };
    }

    try {
      const dir = cwd ?? process.cwd();
      const nodes = await this.executionManager.getAdapter('filesystem').readWorkspace(dir);
      const names = nodes.slice(0, 40).map((n) => `${n.type === 'directory' ? '[dir] ' : ''}${n.name}`);
      return {
        stepId: step.id,
        attempted: true,
        status: 'success',
        detail: `Listed ${nodes.length} entries under ${dir} (safe first step)`,
        stdout: names.join('\n'),
      };
    } catch (err) {
      return {
        stepId: step.id,
        attempted: true,
        status: 'failed',
        detail: err instanceof Error ? err.message : String(err),
      };
    }
  }

  private parseSteps(body: string, maxSteps: number): IntentStep[] {
    try {
      const parsed = JSON.parse(body) as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      const content =
        parsed?.choices?.[0]?.message?.content ??
        (typeof body === 'string' && body.trim().startsWith('{') ? body : null);
      if (!content) return [];

      const json = JSON.parse(content) as {
        steps?: Array<{
          title?: string;
          description?: string;
          suggestedTarget?: IntentStep['suggestedTarget'];
        }>;
      };
      if (!Array.isArray(json.steps)) return [];

      return json.steps.slice(0, maxSteps).map((s, i) => ({
        id: `step_${i + 1}`,
        title: (s.title ?? `Step ${i + 1}`).slice(0, 120),
        description: (s.description ?? '').slice(0, 500),
        suggestedTarget: s.suggestedTarget ?? undefined,
        order: i + 1,
      }));
    } catch {
      return [];
    }
  }

  private fallbackSteps(goal: string): IntentStep[] {
    return [
      {
        id: 'step_1',
        title: 'Inspect workspace',
        description: `List relevant files before acting on: ${goal.slice(0, 200)}`,
        suggestedTarget: 'filesystem',
        order: 1,
      },
      {
        id: 'step_2',
        title: 'Clarify scope',
        description: `Restate success criteria for: ${goal.slice(0, 200)}`,
        suggestedTarget: 'vscode',
        order: 2,
      },
      {
        id: 'step_3',
        title: 'Implement and verify',
        description: 'Apply changes, run checks, summarize outcome.',
        suggestedTarget: 'terminal',
        order: 3,
      },
    ];
  }
}
