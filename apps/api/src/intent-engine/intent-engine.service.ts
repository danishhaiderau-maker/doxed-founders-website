import { Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AiProxyRuntimeService } from '../ai-proxy/ai-proxy-runtime.service';
import { FlightRecorderService } from '../flight-recorder/flight-recorder.service';
import type {
  DecomposeGoalInput,
  IntentDecomposition,
  IntentStep,
} from './intent-engine.types';

const DECOMPOSE_SYSTEM = `You are the Founder OS Intent Engine. Decompose the founder's goal into an ordered list of concrete execution steps.
Return STRICT JSON only (no markdown):
{"steps":[{"title":"...","description":"...","suggestedTarget":"terminal"|"filesystem"|"browser"|"cursor"|"vscode"|null}]}
Keep 3–8 steps. Prefer actionable engineering steps. suggestedTarget is optional.`;

/**
 * Intent Engine — kernel service #10 skeleton.
 *
 * Input:  goal string (+ optional projectId)
 * Decision: ask AI Gateway (via AiProxyRuntime) for a step list
 * Output: IntentDecomposition + Flight Recorder row (intent=agent)
 *
 * Failures degrade to a deterministic 3-step fallback so callers always
 * get a graph shape they can render / hand to Execution Manager later.
 */
@Injectable()
export class IntentEngineService {
  private readonly logger = new Logger(IntentEngineService.name);

  constructor(
    private readonly aiProxy: AiProxyRuntimeService,
    private readonly flightRecorder: FlightRecorderService,
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
    };
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
        title: 'Clarify scope',
        description: `Restate success criteria for: ${goal.slice(0, 200)}`,
        suggestedTarget: 'vscode',
        order: 1,
      },
      {
        id: 'step_2',
        title: 'Inspect workspace',
        description: 'Read relevant files and confirm current state.',
        suggestedTarget: 'filesystem',
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
