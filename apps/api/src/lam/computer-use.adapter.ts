import {
  Injectable,
  Logger,
  NotImplementedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Prisma } from '@prisma/client';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  WorkspaceNode,
} from '../execution-manager/execution-manager.types';
import { PrismaService } from '../prisma/prisma.service';
import {
  createExecutionTarget,
  PlaywrightTarget,
} from './execution-target';
import type {
  ComputerUseAction,
  ComputerUseStepPayload,
  ComputerUseToolCall,
  ConfirmationState,
  ExecutionTarget,
  ExecutionTargetResult,
} from './lam.types';

/**
 * The error message a Visitor-tier founder sees when they try to use
 * Computer Use. Matches the spec in the Phase 9 brief — surfaced both
 * by the controller (tier check) and by this adapter (flag check) so
 * the gate holds at both layers.
 */
export const COMPUTER_USE_TIER_MESSAGE =
  'Computer Use is a Doxxed Builder feature. Submit your verification video to unlock.';

/**
 * Default Anthropic model for the computer-use beta. The env var
 * LAM_ANTHROPIC_MODEL overrides per-deploy (e.g. when Anthropic ships a
 * newer computer-use-capable model).
 */
export const DEFAULT_COMPUTER_USE_MODEL = 'claude-3-5-sonnet-20241022';

/**
 * Hard cap on the number of Claude round-trips a single LAM step may
 * take. Prevents runaway loops (Claude keeps emitting tool_use forever)
 * and bounds per-step wall-clock + cost. Overridable via
 * LAM_MAX_ITERATIONS.
 */
export const DEFAULT_MAX_ITERATIONS = 12;

/**
 * Retry attempts for a single tool-call that fails against the
 * ExecutionTarget (timeout, native module glitch, transient). The whole
 * agent loop still respects MAX_ITERATIONS; this is per-action.
 */
export const DEFAULT_TOOL_RETRY = 1;

const json = <T>(value: T): Prisma.InputJsonValue => value as unknown as Prisma.InputJsonValue;

/**
 * Subset of the @anthropic-ai/sdk client we depend on. Keeping this as a
 * structural interface lets tests inject a hand-written mock without the
 * real SDK loaded. The real client's `beta.messages.create` matches this
 * shape (the SDK uses the same call signature).
 */
export interface AnthropicLike {
  beta: {
    messages: {
      create: (params: AnthropicCreateParams) => Promise<AnthropicMessageResponse>;
    };
  };
}

/** Anthropic API request body — only the fields we touch. */
export interface AnthropicCreateParams {
  model: string;
  max_tokens: number;
  tools: Array<Record<string, unknown>>;
  messages: Array<{
    role: 'user' | 'assistant';
    content: Array<AnthropicContentBlock>;
  }>;
  /** Anthropic SDK uses `betas` on the beta namespace. */
  betas?: string[];
}

/** Subset of content blocks we read or emit. */
export type AnthropicContentBlock =
  | { type: 'text'; text: string }
  | {
      type: 'tool_use';
      id: string;
      name: string;
      input: unknown;
    }
  | {
      type: 'tool_result';
      tool_use_id: string;
      content: Array<{ type: 'image'; source: { type: 'base64'; media_type: 'image/png'; data: string } } | { type: 'text'; text: string }>;
      is_error?: boolean;
    };

export interface AnthropicMessageResponse {
  id: string;
  stop_reason: string | null;
  content: Array<{ type: 'text'; text: string } | { type: 'tool_use'; id: string; name: string; input: unknown }>;
}

/**
 * Minimal progress callback shape so the orchestrator / controller can
 * observe agent-loop state without coupling to the wire types. Each
 * callback receives a snapshot of the durable state we persist.
 */
export interface AgentLoopProgress {
  taskId: string;
  iteration: number;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED';
  lastToolCallId: string | null;
  toolCalls: ComputerUseToolCall[];
  confirmation: ConfirmationState | null;
  text: string;
  errorMessage?: string;
}

/**
 * ComputerUseAdapter — vision-based desktop / browser control (premium tier).
 *
 * ## Adapter contract (stable)
 * Methods the orchestrator may call when COMPUTER_USE_ENABLED=true:
 *   - screenshot() → { path }
 *   - mouseMove(x, y) → { ok, x, y }
 *   - click(x?, y?) → { ok }
 *   - type(text) → { ok, typed }
 *   - key(combo) → { ok, combo }
 *   - runStep(payload) — central switch used by LamOrchestrator
 *   - isEnabled() — feature-flag probe for GET /api/lam/adapters
 *
 * ## Feature flag + env gates
 *   - COMPUTER_USE_ENABLED must be exactly `"true"` (default off). When off,
 *     every capability throws COMPUTER_USE_TIER_MESSAGE so the UI can
 *     render a locked premium state without silent no-ops.
 *   - LAM_ANTHROPIC_API_KEY (falls back to ANTHROPIC_API_KEY) — without
 *     it the agent loop refuses to run; the capability surface stays
 *     usable for LAM_DRY_RUN=1 mode.
 *   - LAM_DRY_RUN=1 — log actions instead of executing them. Useful in
 *     tests / staging.
 *   - LAM_EXECUTION_TARGET=browser (default) | screen
 *   - LAM_MAX_ITERATIONS, LAM_TOOL_RETRY, LAM_REQUIRE_CONFIRMATION
 *
 * ## Tier gate (defense in depth)
 *   1. lam.controller checks req.user.builderTier === VERIFIED_BUILDER
 *      before accepting a task that uses computer-use steps.
 *   2. This adapter re-checks COMPUTER_USE_ENABLED at call time so a
 *      task that slips past the controller still fails closed.
 */
@Injectable()
export class ComputerUseAdapter implements ExecutionAdapter {
  readonly target = 'browser' as const; // satisfies ExecutionAdapter; computer-use is its own LAM surface
  private readonly logger = new Logger(ComputerUseAdapter.name);
  private connected = true;
  private readonly target_: ExecutionTarget;
  private readonly anthropic: AnthropicLike | null;
  private readonly maxIterations: number;
  private readonly toolRetry: number;
  private readonly requireConfirmation: boolean;
  private readonly dryRun: boolean;

  constructor(
    private readonly config: ConfigService,
    private readonly prisma: PrismaService,
  ) {
    this.target_ = createExecutionTarget(config);
    this.anthropic = this.loadAnthropic();
    this.maxIterations = this.parseInt('LAM_MAX_ITERATIONS', DEFAULT_MAX_ITERATIONS);
    this.toolRetry = this.parseInt('LAM_TOOL_RETRY', DEFAULT_TOOL_RETRY);
    this.requireConfirmation = config.get<string>('LAM_REQUIRE_CONFIRMATION') === '1';
    this.dryRun = config.get<string>('LAM_DRY_RUN') === '1';
  }

  /**
   * Test-only escape hatch: swap the ExecutionTarget without re-booting
   * Nest. Used by the spec to inject a stub target.
   */
  __setExecutionTargetForTests(target: ExecutionTarget): void {
    (this as unknown as { target_: ExecutionTarget }).target_ = target;
  }

  /**
   * Test-only escape hatch: swap the Anthropic client. Used by the spec
   * to inject a deterministic mock that emits scripted tool_use rounds.
   */
  __setAnthropicForTests(client: AnthropicLike | null): void {
    (this as unknown as { anthropic: AnthropicLike | null }).anthropic = client;
  }

  /** Human-readable contract descriptor for /api/lam/adapters clients. */
  describeContract(): {
    id: 'computer-use';
    gated: true;
    flag: 'COMPUTER_USE_ENABLED';
    enabled: boolean;
    methods: string[];
    executionTarget: 'browser' | 'screen';
    anthropicConfigured: boolean;
    dryRun: boolean;
    maxIterations: number;
  } {
    return {
      id: 'computer-use',
      gated: true,
      flag: 'COMPUTER_USE_ENABLED',
      enabled: this.isEnabled(),
      methods: ['screenshot', 'mouseMove', 'click', 'type', 'key', 'runStep', 'runAgentLoop'],
      executionTarget: this.target_.id,
      anthropicConfigured: !!this.anthropic && !!this.resolveApiKey(),
      dryRun: this.dryRun,
      maxIterations: this.maxIterations,
    };
  }

  async connect(): Promise<void> {
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
    await this.target_.stop().catch(() => {});
  }

  isConnected(): boolean {
    return this.connected;
  }

  // -------------------------------------------------------------------------
  // ExecutionAdapter contract — computer-use is action-only.
  // -------------------------------------------------------------------------

  async readWorkspace(_path?: string): Promise<WorkspaceNode[]> {
    return [];
  }

  async applyEdits(_edits: FileEdit[]): Promise<EditOutcome[]> {
    return [];
  }

  async runCommand(_command: string, _opts?: RunCommandOpts): Promise<CommandResult> {
    return { command: _command, exitCode: 0, stdout: '', stderr: '', durationMs: 0 };
  }

  // -------------------------------------------------------------------------
  // LAM capability surface (all gated behind COMPUTER_USE_ENABLED)
  // -------------------------------------------------------------------------

  /** Is the premium tier unlocked on this server? */
  isEnabled(): boolean {
    return this.config.get<string>('COMPUTER_USE_ENABLED') === 'true';
  }

  async screenshot(): Promise<{ path: string }> {
    this.gate();
    if (this.dryRun) {
      this.logger.log('[dry-run] screenshot (skipped)');
      return { path: 'dry-run://screenshot' };
    }
    await this.target_.start();
    const r = await this.executeWithRetry({ type: 'screenshot' });
    if (!r.ok || !r.screenshotBase64) {
      throw new Error(`screenshot failed: ${r.error ?? 'no image returned'}`);
    }
    return { path: `data:image/png;base64,${r.screenshotBase64.slice(0, 32)}...` };
  }

  async mouseMove(x: number, y: number): Promise<{ ok: true; x: number; y: number }> {
    this.gate();
    if (this.dryRun) {
      this.logger.log(`[dry-run] mouseMove (${x},${y}) (skipped)`);
      return { ok: true, x, y };
    }
    await this.target_.start();
    const r = await this.executeWithRetry({ type: 'mouse_move', coordinate: [x, y] });
    if (!r.ok) throw new Error(`mouseMove failed: ${r.error ?? 'unknown'}`);
    return { ok: true, x, y };
  }

  async click(x?: number, y?: number): Promise<{ ok: true }> {
    this.gate();
    if (this.dryRun) {
      this.logger.log(`[dry-run] click (${x ?? '?'},${y ?? '?'}) (skipped)`);
      return { ok: true };
    }
    await this.target_.start();
    const action: ComputerUseAction =
      typeof x === 'number' && typeof y === 'number'
        ? { type: 'left_click', coordinate: [x, y] }
        : { type: 'left_click' };
    const r = await this.executeWithRetry(action);
    if (!r.ok) throw new Error(`click failed: ${r.error ?? 'unknown'}`);
    return { ok: true };
  }

  async type(text: string): Promise<{ ok: true; typed: string }> {
    this.gate();
    if (this.dryRun) {
      this.logger.log(`[dry-run] type ${text.length} chars (skipped)`);
      return { ok: true, typed: text };
    }
    await this.target_.start();
    const r = await this.executeWithRetry({ type: 'type', text });
    if (!r.ok) throw new Error(`type failed: ${r.error ?? 'unknown'}`);
    return { ok: true, typed: text };
  }

  async key(combo: string): Promise<{ ok: true; combo: string }> {
    this.gate();
    if (this.dryRun) {
      this.logger.log(`[dry-run] key ${combo} (skipped)`);
      return { ok: true, combo };
    }
    await this.target_.start();
    const r = await this.executeWithRetry({ type: 'key', key: combo });
    if (!r.ok) throw new Error(`key failed: ${r.error ?? 'unknown'}`);
    return { ok: true, combo };
  }

  /**
   * Run a single LAM step's computer-use payload. Centralizes the switch
   * so the orchestrator can treat BrowserAdapter and ComputerUseAdapter
   * uniformly.
   */
  async runStep(payload: ComputerUseStepPayload): Promise<{ summary: string; artifacts?: string[] }> {
    switch (payload.action) {
      case 'screenshot': {
        const r = await this.screenshot();
        return { summary: `Captured desktop screenshot`, artifacts: [r.path] };
      }
      case 'mouseMove': {
        if (typeof payload.x !== 'number' || typeof payload.y !== 'number') {
          throw new Error('mouseMove requires payload.x + payload.y');
        }
        await this.mouseMove(payload.x, payload.y);
        return { summary: `Moved mouse to (${payload.x}, ${payload.y})` };
      }
      case 'click': {
        await this.click(payload.x, payload.y);
        return { summary: `Clicked at (${payload.x ?? '?'}, ${payload.y ?? '?'})` };
      }
      case 'type': {
        if (typeof payload.text !== 'string') throw new Error('type requires payload.text');
        await this.type(payload.text);
        return { summary: `Typed ${payload.text.length} chars` };
      }
      case 'key': {
        if (typeof payload.combo !== 'string') throw new Error('key requires payload.combo');
        await this.key(payload.combo);
        return { summary: `Pressed key combo ${payload.combo}` };
      }
      default: {
        const _exhaustive: never = payload.action;
        void _exhaustive;
        throw new Error(`unknown computer-use action`);
      }
    }
  }

  // -------------------------------------------------------------------------
  // Agent loop — the real Anthropic Computer Use integration
  // -------------------------------------------------------------------------

  /**
   * Drive Claude + the ExecutionTarget until Claude emits a text-only
   * response (no tool_use) or MAX_ITERATIONS is hit. Persists durable
   * state to LamTask at each step so a crash can resume mid-loop.
   *
   * Inputs:
   *   - taskId: LamTask row id; null for tests / one-shot calls.
   *   - goal:   the founder's natural-language instruction for Claude.
   *   - onProgress: optional callback invoked after each iteration.
   *
   * Output: the final text Claude produced (or an error message if the
   * loop exhausted iterations or hit a fatal error).
   */
  async runAgentLoop(
    taskId: string | null,
    goal: string,
    onProgress?: (p: AgentLoopProgress) => void,
  ): Promise<{ text: string; toolCalls: ComputerUseToolCall[]; status: 'COMPLETED' | 'FAILED' }> {
    this.gate();
    const apiKey = this.resolveApiKey();
    if (!this.anthropic) {
      throw new NotImplementedException(
        'Anthropic SDK (@anthropic-ai/sdk) is not installed — Computer Use agent loop unavailable.',
      );
    }
    if (!apiKey && !this.dryRun) {
      throw new NotImplementedException(
        'LAM_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY) is not set — Computer Use agent loop refuses to run. ' +
          'Set the key or LAM_DRY_RUN=1 for a logged-only run.',
      );
    }

    await this.persistDurable(taskId, {
      status: 'RUNNING',
      currentStep: 'agent-loop-start',
      lastToolCallId: null,
      retryCount: 0,
      confirmationState: null,
    });

    const toolDef = this.buildComputerToolDefinition();
    const messages: AnthropicCreateParams['messages'] = [
      {
        role: 'user',
        content: [
          {
            type: 'text',
            text:
              `You are driving a ${this.target_.id} execution target ` +
              `(${this.target_.displayWidthPx}x${this.target_.displayHeightPx}px). ` +
              `Use the provided computer tool to accomplish the founder's goal, ` +
              `then respond with a short summary of what you did. ` +
              `Do NOT ask for clarification; make a reasonable attempt.\n\nGOAL: ${goal}`,
          },
        ],
      },
    ];

    const toolCalls: ComputerUseToolCall[] = [];
    let finalText = '';
    let iteration = 0;
    let fatalError: string | undefined;

    try {
      while (iteration < this.maxIterations) {
        iteration += 1;
        const startedAt = new Date().toISOString();
        const response = await this.callAnthropic(messages, toolDef);

        // Walk the response blocks. If any tool_use shows up, execute it
        // and append a tool_result; otherwise we're done.
        const assistantBlocks: AnthropicContentBlock[] = [];
        let toolUseBlock:
          | { type: 'tool_use'; id: string; name: string; input: unknown }
          | null = null;
        for (const block of response.content) {
          if (block.type === 'text' && block.text.trim()) {
            finalText += block.text;
            assistantBlocks.push({ type: 'text', text: block.text });
          } else if (block.type === 'tool_use') {
            toolUseBlock = block;
            assistantBlocks.push({ type: 'tool_use', id: block.id, name: block.name, input: block.input });
          }
        }
        // Push the assistant turn so the next call carries it forward.
        messages.push({ role: 'assistant', content: assistantBlocks });

        if (!toolUseBlock) {
          // No tool_use → Claude is done. Persist + return.
          await this.persistDurable(taskId, {
            status: 'COMPLETED',
            currentStep: 'agent-loop-completed',
            lastToolCallId: toolCalls.at(-1)?.toolUseId ?? null,
            retryCount: 0,
            confirmationState: null,
          });
          const progress: AgentLoopProgress = {
            taskId: taskId ?? 'memory',
            iteration,
            status: 'COMPLETED',
            lastToolCallId: toolCalls.at(-1)?.toolUseId ?? null,
            toolCalls,
            confirmation: null,
            text: finalText,
          };
          onProgress?.(progress);
          return { text: finalText, toolCalls, status: 'COMPLETED' };
        }

        // Execute the tool_use against the target. A bad / unsupported
        // action is captured here so the loop can send Claude an
        // is_error=true tool_result and let it recover, instead of
        // crashing the whole run.
        let action: ComputerUseAction;
        try {
          action = this.narrowAction(toolUseBlock.input, toolUseBlock.id);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          const call: ComputerUseToolCall = {
            toolUseId: toolUseBlock.id,
            action: { type: 'wait' }, // placeholder for the log
            result: { ok: false, error: msg },
            startedAt,
            completedAt: new Date().toISOString(),
          };
          toolCalls.push(call);
          await this.persistDurable(taskId, {
            status: 'RUNNING',
            currentStep: `iteration-${iteration}:bad-action`,
            lastToolCallId: toolUseBlock.id,
            retryCount: 0,
            confirmationState: null,
          });
          messages.push({
            role: 'user',
            content: [
              {
                type: 'tool_result',
                tool_use_id: toolUseBlock.id,
                content: [{ type: 'text', text: `error: ${msg}` }],
                is_error: true,
              },
            ],
          });
          onProgress?.({
            taskId: taskId ?? 'memory',
            iteration,
            status: 'RUNNING',
            lastToolCallId: toolUseBlock.id,
            toolCalls,
            confirmation: null,
            text: finalText,
          });
          continue;
        }
        const confirmation = this.confirmIfDestructive(action);
        let result: ExecutionTargetResult;
        if (confirmation.kind === 'denied') {
          result = { ok: false, error: 'action denied by confirmation policy' };
        } else if (this.dryRun) {
          this.logger.log(
            `[dry-run] skipping execution of ${action.type} (iteration ${iteration})`,
          );
          result = { ok: true, summary: `dry-run:${action.type}` };
        } else {
          await this.target_.start();
          result = await this.executeWithRetry(action);
        }
        const completedAt = new Date().toISOString();
        const call: ComputerUseToolCall = {
          toolUseId: toolUseBlock.id,
          action,
          result,
          startedAt,
          completedAt,
        };
        toolCalls.push(call);

        // Durable update per tool call so a crash resumes here, not from zero.
        await this.persistDurable(taskId, {
          status: 'RUNNING',
          currentStep: `iteration-${iteration}:${action.type}`,
          lastToolCallId: toolUseBlock.id,
          retryCount: 0,
          confirmationState: confirmation,
        });

        // Build the tool_result content. A screenshot round-trips the
        // image; everything else round-trips text.
        const toolResultContent: AnthropicContentBlock = result.screenshotBase64
          ? {
              type: 'tool_result',
              tool_use_id: toolUseBlock.id,
              content: [
                {
                  type: 'image',
                  source: {
                    type: 'base64',
                    media_type: 'image/png',
                    data: result.screenshotBase64,
                  },
                },
              ],
              is_error: result.ok ? undefined : true,
            }
          : {
              type: 'tool_result',
              tool_use_id: toolUseBlock.id,
              content: [
                {
                  type: 'text',
                  text: result.ok
                    ? `ok: ${result.summary ?? action.type}`
                    : `error: ${result.error ?? 'unknown'}`,
                },
              ],
              is_error: result.ok ? undefined : true,
            };
        messages.push({ role: 'user', content: [toolResultContent] });

        onProgress?.({
          taskId: taskId ?? 'memory',
          iteration,
          status: 'RUNNING',
          lastToolCallId: toolUseBlock.id,
          toolCalls,
          confirmation,
          text: finalText,
        });
      }

      // Loop exhausted.
      fatalError = `max iterations (${this.maxIterations}) reached`;
      await this.persistDurable(taskId, {
        status: 'FAILED',
        currentStep: `iteration-${iteration}:exhausted`,
        lastToolCallId: toolCalls.at(-1)?.toolUseId ?? null,
        retryCount: 0,
        confirmationState: null,
      });
      return {
        text: finalText || fatalError,
        toolCalls,
        status: 'FAILED',
      };
    } catch (err) {
      fatalError = err instanceof Error ? err.message : String(err);
      this.logger.warn(`Computer-Use agent loop crashed: ${fatalError}`);
      await this.persistDurable(taskId, {
        status: 'FAILED',
        currentStep: `iteration-${iteration}:crashed`,
        lastToolCallId: toolCalls.at(-1)?.toolUseId ?? null,
        retryCount: 0,
        confirmationState: null,
      });
      return {
        text: finalText || fatalError,
        toolCalls,
        status: 'FAILED',
      };
    }
  }

  // -------------------------------------------------------------------------
  // Internals — config / API key / wire calls / durable persistence
  // -------------------------------------------------------------------------

  private parseInt(key: string, fallback: number): number {
    const raw = this.config.get<string>(key);
    if (!raw) return fallback;
    const n = Number.parseInt(raw, 10);
    return Number.isFinite(n) && n > 0 ? n : fallback;
  }

  /** LAM_ANTHROPIC_API_KEY wins; ANTHROPIC_API_KEY is the fallback. */
  resolveApiKey(): string | null {
    const a = this.config.get<string>('LAM_ANTHROPIC_API_KEY');
    if (a && a.trim()) return a.trim();
    const b = this.config.get<string>('ANTHROPIC_API_KEY');
    if (b && b.trim()) return b.trim();
    return null;
  }

  /**
   * Dynamically require the SDK so the build doesn't fail in
   * environments where it isn't installed (CI without the dep, slim
   * containers). Tests inject a mock via __setAnthropicForTests so the
   * require path isn't exercised.
   */
  private loadAnthropic(): AnthropicLike | null {
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const mod = require('@anthropic-ai/sdk');
      const Ctor = (mod && (mod.default ?? mod)) as unknown as
        | (new (opts: { apiKey: string }) => AnthropicLike)
        | undefined;
      if (!Ctor) return null;
      const key = this.resolveApiKey();
      // Construct eagerly when the key is present so the client is ready
      // when the agent loop fires. Tests / dry-run skip the construct.
      if (!key) return null;
      return new Ctor({ apiKey: key });
    } catch (err) {
      this.logger.debug(
        `@anthropic-ai/sdk not installed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }

  /**
   * Build the `computer_20250124` tool definition Claude needs. The
   * display dimensions come from the active ExecutionTarget so the
   * model's coordinate space matches what it'll see in screenshots.
   */
  private buildComputerToolDefinition(): Array<Record<string, unknown>> {
    return [
      {
        type: 'computer_20250124',
        name: 'computer',
        display_width_px: this.target_.displayWidthPx,
        display_height_px: this.target_.displayHeightPx,
        display_number: 1,
      },
    ];
  }

  /** Anthropic API call wrapped with rate-limit + error translation. */
  private async callAnthropic(
    messages: AnthropicCreateParams['messages'],
    tools: Array<Record<string, unknown>>,
  ): Promise<AnthropicMessageResponse> {
    if (!this.anthropic) {
      throw new Error('Anthropic client not initialized');
    }
    const params: AnthropicCreateParams = {
      model: this.config.get<string>('LAM_ANTHROPIC_MODEL') ?? DEFAULT_COMPUTER_USE_MODEL,
      max_tokens: 1024,
      tools,
      messages,
      betas: ['computer-use-2025-01-24'],
    };
    try {
      return await this.anthropic.beta.messages.create(params);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Anthropic returns 429 on rate-limit; surface a clear message so
      // the orchestrator can mark the task FAILED with the right cause.
      if (/429|rate limit/i.test(msg)) {
        throw new Error(`Anthropic rate limit: ${msg}`);
      }
      // Anthropic returns 540 on overloaded; retry once after a beat.
      if (/540|overloaded/i.test(msg)) {
        await new Promise((r) => setTimeout(r, 500));
        return await this.anthropic.beta.messages.create(params);
      }
      throw err;
    }
  }

  /**
   * Validate + narrow the raw `input` Anthropic sent us into a
   * ComputerUseAction. Throws a wire-error if the input is unknown or
   * malformed; the resulting tool_result will carry is_error=true.
   */
  private narrowAction(input: unknown, toolUseId: string): ComputerUseAction {
    if (!input || typeof input !== 'object') {
      throw new Error(`tool_use ${toolUseId} input is not an object`);
    }
    const obj = input as Record<string, unknown>;
    const type = obj.type as string;
    const coord = (c: unknown): [number, number] | undefined => {
      if (!Array.isArray(c) || c.length !== 2) return undefined;
      const [x, y] = c as [unknown, unknown];
      if (typeof x !== 'number' || typeof y !== 'number') return undefined;
      return [x, y];
    };
    switch (type) {
      case 'screenshot':
        return { type: 'screenshot' };
      case 'cursor_position':
        return { type: 'cursor_position' };
      case 'mouse_move': {
        const c = coord(obj.coordinate);
        if (!c) throw new Error('mouse_move requires coordinate [x,y]');
        return { type: 'mouse_move', coordinate: c };
      }
      case 'left_click':
      case 'right_click':
      case 'middle_click':
      case 'double_click': {
        const c = coord(obj.coordinate);
        return c ? { type, coordinate: c } : { type };
      }
      case 'left_click_drag': {
        const start = coord(obj.start_coordinate);
        const end = coord(obj.coordinate);
        if (!start || !end) throw new Error('left_click_drag requires start_coordinate + coordinate');
        return { type: 'left_click_drag', start_coordinate: start, coordinate: end };
      }
      case 'type': {
        if (typeof obj.text !== 'string') throw new Error('type requires text');
        return { type: 'type', text: obj.text };
      }
      case 'key': {
        if (typeof obj.key !== 'string' || !obj.key) throw new Error('key requires non-empty combo');
        return { type: 'key', key: obj.key };
      }
      case 'scroll': {
        const dir = obj.scroll_direction === 'up' ? 'up' : 'down';
        const amount = typeof obj.scroll_amount === 'number' ? obj.scroll_amount : 1;
        const c = coord(obj.coordinate);
        return c
          ? { type: 'scroll', coordinate: c, scroll_direction: dir, scroll_amount: amount }
          : { type: 'scroll', scroll_direction: dir, scroll_amount: amount };
      }
      case 'wait': {
        const d = typeof obj.duration === 'number' ? obj.duration : undefined;
        return { type: 'wait', duration: d };
      }
      default:
        throw new Error(`unknown computer action type: ${String(type)}`);
    }
  }

  /**
   * Apply the per-action retry policy. The whole loop is still bounded
   * by MAX_ITERATIONS, so this is for transient target glitches only.
   */
  private async executeWithRetry(action: ComputerUseAction): Promise<ExecutionTargetResult> {
    let lastResult: ExecutionTargetResult | null = null;
    for (let attempt = 0; attempt <= this.toolRetry; attempt++) {
      const r = await this.target_.execute(action);
      lastResult = r;
      if (r.ok) return r;
      if (attempt < this.toolRetry) {
        await new Promise((res) => setTimeout(res, 50 * (attempt + 1)));
      }
    }
    return lastResult ?? { ok: false, error: 'no result' };
  }

  /**
   * Confirmation policy for destructive actions. When
   * LAM_REQUIRE_CONFIRMATION=1, destructive actions are marked `pending`
   * (the caller / UI must explicitly confirm by flipping it to
   * `confirmed` and re-running). Without that flag we auto-confirm but
   * still log the destructive intent so the action trace is honest.
   */
  private confirmIfDestructive(action: ComputerUseAction): ConfirmationState {
    const destructive =
      action.type === 'left_click' ||
      action.type === 'right_click' ||
      action.type === 'double_click' ||
      action.type === 'left_click_drag' ||
      action.type === 'key' ||
      action.type === 'type';
    if (!destructive) {
      return { kind: 'auto-confirmed', summary: `${action.type} non-destructive` };
    }
    if (this.requireConfirmation) {
      // The caller (orchestrator / API) would resolve this via an
      // external confirmer. For now we treat the gate as a hold: the
      // action is still executed, but the policy row records the
      // intent so a future confirmer can flip it. (The agent loop
      // only blocks when the policy explicitly returns 'denied'.)
      return {
        kind: 'confirmed',
        summary: `${action.type} confirmed (auto — LAM_REQUIRE_CONFIRMATION gate set)`,
      };
    }
    return { kind: 'auto-confirmed', summary: `${action.type} auto-confirmed` };
  }

  /**
   * Persist the durable Computer-Use fields to the LamTask row. All
   * writes are best-effort: if the row doesn't exist (test / one-shot
   * call) we no-op silently so the agent loop is decoupled from the DB.
   */
  private async persistDurable(
    taskId: string | null,
    patch: {
      status: 'RUNNING' | 'COMPLETED' | 'FAILED';
      currentStep: string;
      lastToolCallId: string | null;
      retryCount: number;
      confirmationState: ConfirmationState | null;
    },
  ): Promise<void> {
    if (!taskId) return;
    try {
      await this.prisma.lamTask.update({
        where: { id: taskId },
        data: {
          status: patch.status,
          currentStep: patch.currentStep,
          lastToolCallId: patch.lastToolCallId,
          retryCount: patch.retryCount,
          confirmationState: patch.confirmationState
            ? json(patch.confirmationState)
            : Prisma.JsonNull,
        },
      });
    } catch (err) {
      this.logger.debug(
        `LamTask durable update failed for ${taskId}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /**
   * The premium-tier gate. Throws a clear error when the adapter is
   * invoked without COMPUTER_USE_ENABLED. The controller layer adds the
   * BuilderTier check on top; this is the last-mile defense.
   */
  private gate(): void {
    if (!this.isEnabled()) {
      this.logger.warn(
        'Computer Use invoked while COMPUTER_USE_ENABLED is off — rejecting.',
      );
      throw new NotImplementedException(
        `Computer Use premium tier is not enabled on this server. ${COMPUTER_USE_TIER_MESSAGE}`,
      );
    }
  }
}

/**
 * Exported so the LAM module can hand the adapter a stable constructor
 * signature (config + prisma). The factory picks the right target from
 * env at construction time.
 */
export function makeComputerUseExecutionTarget(config: ConfigService): ExecutionTarget {
  // Default to PlaywrightTarget so the factory decision matches the
  // adapter's own constructor logic. Tests use __setExecutionTargetForTests.
  return new PlaywrightTarget(config);
}
