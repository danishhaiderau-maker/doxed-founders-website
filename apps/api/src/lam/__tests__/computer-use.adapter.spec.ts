/**
 * Unit tests for the Phase 9 ComputerUseAdapter.
 *
 * Covers:
 *   1. Feature-flag gate — capability methods fail closed without
 *      COMPUTER_USE_ENABLED=true.
 *   2. Dry-run mode — LAM_DRY_RUN=1 logs and short-circuits execution.
 *   3. Agent loop — verify the tool_use → execute → tool_result cycle:
 *        - one screenshot, one click, then a final text → COMPLETED.
 *        - screenshot round-trips base64 PNG back to Claude.
 *        - max iterations bounds the loop.
 *        - unknown action type surfaces is_error=true in the next round.
 *        - rate-limit error fails the task with a clear message.
 *   4. Durable persistence — every iteration writes LamTask fields
 *      (currentStep / lastToolCallId / confirmationState).
 *   5. describeContract reports the real surface, not the stub.
 *
 * The Anthropic client is injected via __setAnthropicForTests with a
 * scripted mock so the agent loop is fully deterministic. Prisma is an
 * in-memory stub mirroring the slice the adapter consumes.
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { ConfigService } from '@nestjs/config';
import {
  ComputerUseAdapter,
  COMPUTER_USE_TIER_MESSAGE,
  type AgentLoopProgress,
  type AnthropicContentBlock,
  type AnthropicCreateParams,
  type AnthropicLike,
  type AnthropicMessageResponse,
} from '../computer-use.adapter';
import type {
  ComputerUseAction,
  ExecutionTarget,
  ExecutionTargetResult,
} from '../lam.types';

// ---------------------------------------------------------------------------
// Stubs
// ---------------------------------------------------------------------------

type LamTaskRow = {
  id: string;
  status: string;
  currentStep: string | null;
  lastToolCallId: string | null;
  retryCount: number;
  confirmationState: unknown;
};

function makeStubPrisma() {
  const tasks: LamTaskRow[] = [];
  return {
    lamTask: {
      update: async ({ where, data }: { where: { id: string }; data: Partial<LamTaskRow> }) => {
        const row = tasks.find((t) => t.id === where.id);
        if (row) Object.assign(row, data);
        return row;
      },
      create: async ({ data }: { data: Partial<LamTaskRow> }) => {
        const row: LamTaskRow = {
          id: data.id ?? 'task-1',
          status: data.status ?? 'PLANNING',
          currentStep: null,
          lastToolCallId: null,
          retryCount: 0,
          confirmationState: null,
        };
        tasks.push(row);
        return row;
      },
    },
    _tasks: tasks,
  };
}

type ConfigMap = Record<string, string | undefined>;

function makeConfig(map: ConfigMap): ConfigService {
  return {
    get: <T = string>(key: string): T | undefined => map[key] as T | undefined,
  } as unknown as ConfigService;
}

/** A test-only ExecutionTarget that records every action + can be scripted. */
class FakeTarget implements ExecutionTarget {
  readonly id = 'browser' as const;
  readonly displayWidthPx = 800;
  readonly displayHeightPx = 600;
  public calls: ComputerUseAction[] = [];
  public running = false;
  public scripted: Partial<Record<ComputerUseAction['type'], ExecutionTargetResult>> = {};

  scriptAction(
    type: ComputerUseAction['type'],
    result: ExecutionTargetResult,
  ): void {
    this.scripted[type] = result;
  }

  async start(): Promise<void> {
    this.running = true;
  }
  async stop(): Promise<void> {
    this.running = false;
  }
  isRunning(): boolean {
    return this.running;
  }
  async execute(action: ComputerUseAction): Promise<ExecutionTargetResult> {
    this.calls.push(action);
    if (this.scripted[action.type]) {
      return this.scripted[action.type]!;
    }
    if (action.type === 'screenshot') {
      return { ok: true, summary: 'screenshot', screenshotBase64: 'ZmFrZS1wbmctYnl0ZXM=' };
    }
    return { ok: true, summary: action.type };
  }
}

/**
 * Scripted Anthropic client. Pass an array of "rounds" — each round is
 * the response Claude would emit. The mock pops them in order. Records
 * every call so the test can assert tool_result shape.
 */
function makeScriptedAnthropic(
  rounds: AnthropicMessageResponse[],
): AnthropicLike & { calls: AnthropicCreateParams[] } {
  const queue = [...rounds];
  const calls: AnthropicCreateParams[] = [];
  return {
    calls,
    beta: {
      messages: {
        create: async (params: AnthropicCreateParams): Promise<AnthropicMessageResponse> => {
          // Snapshot the messages at call time — the caller mutates the
          // same array across iterations, so without a deep copy the
          // recorded entry would reflect the final state, not the
          // state when Anthropic was actually called.
          calls.push({
            ...params,
            messages: params.messages.map((m: AnthropicCreateParams['messages'][number]) => ({
              role: m.role,
              content: m.content.map((b: AnthropicContentBlock) => ({ ...b })) as never,
            })),
          });
          const next = queue.shift();
          if (!next) throw new Error('test fixture: scripted rounds exhausted');
          return next;
        },
      },
    },
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ComputerUseAdapter — feature-flag gate', () => {
  it('isEnabled() reflects COMPUTER_USE_ENABLED', () => {
    const a = new ComputerUseAdapter(makeConfig({}), makeStubPrisma() as never);
    assert.equal(a.isEnabled(), false);
    const b = new ComputerUseAdapter(
      makeConfig({ COMPUTER_USE_ENABLED: 'true' }),
      makeStubPrisma() as never,
    );
    assert.equal(b.isEnabled(), true);
  });

  it('screenshot() throws COMPUTER_USE_TIER_MESSAGE when disabled', async () => {
    const a = new ComputerUseAdapter(makeConfig({}), makeStubPrisma() as never);
    await assert.rejects(() => a.screenshot(), (err: unknown) => {
      const e = err as Error;
      return e.message.includes(COMPUTER_USE_TIER_MESSAGE);
    });
  });

  it('click / type / key / mouseMove all throw when disabled', async () => {
    const a = new ComputerUseAdapter(makeConfig({}), makeStubPrisma() as never);
    for (const op of [
      () => a.click(0, 0),
      () => a.type('hi'),
      () => a.key('Return'),
      () => a.mouseMove(1, 2),
      () => a.runStep({ action: 'screenshot' }),
    ]) {
      await assert.rejects(op, (err: unknown) => {
        const e = err as Error;
        return e.message.includes(COMPUTER_USE_TIER_MESSAGE);
      });
    }
  });

  it('describeContract reports the real surface (not the stub)', () => {
    const a = new ComputerUseAdapter(
      makeConfig({ COMPUTER_USE_ENABLED: 'true' }),
      makeStubPrisma() as never,
    );
    const c = a.describeContract();
    assert.equal(c.id, 'computer-use');
    assert.equal(c.gated, true);
    assert.equal(c.flag, 'COMPUTER_USE_ENABLED');
    assert.equal(c.enabled, true);
    assert.ok(c.methods.includes('runAgentLoop'));
    assert.ok(c.methods.includes('screenshot'));
    assert.equal(c.executionTarget, 'browser');
  });
});

describe('ComputerUseAdapter — dry-run mode', () => {
  let adapter: ComputerUseAdapter;
  let target: FakeTarget;

  beforeEach(() => {
    target = new FakeTarget();
    adapter = new ComputerUseAdapter(
      makeConfig({ COMPUTER_USE_ENABLED: 'true', LAM_DRY_RUN: '1' }),
      makeStubPrisma() as never,
    );
    adapter.__setExecutionTargetForTests(target);
  });

  it('screenshot() short-circuits with a dry-run:// path', async () => {
    const r = await adapter.screenshot();
    assert.equal(r.path.startsWith('dry-run://'), true);
    assert.equal(target.calls.length, 0);
  });

  it('type / click / key / mouseMove all short-circuit', async () => {
    await adapter.type('hello');
    await adapter.click(10, 20);
    await adapter.key('Return');
    await adapter.mouseMove(1, 2);
    assert.equal(target.calls.length, 0);
  });

  it('dry-run still resolves the capability methods', async () => {
    const r = await adapter.runStep({ action: 'type', text: 'hi' });
    assert.match(r.summary, /Typed 2 chars/);
  });
});

describe('ComputerUseAdapter — agent loop with mock Anthropic client', () => {
  let adapter: ComputerUseAdapter;
  let target: FakeTarget;
  let prisma: ReturnType<typeof makeStubPrisma>;

  beforeEach(() => {
    target = new FakeTarget();
    prisma = makeStubPrisma();
    adapter = new ComputerUseAdapter(
      makeConfig({
        COMPUTER_USE_ENABLED: 'true',
        LAM_ANTHROPIC_API_KEY: 'test-key',
        LAM_MAX_ITERATIONS: '5',
      }),
      prisma as never,
    );
    adapter.__setExecutionTargetForTests(target);
  });

  it('runs a screenshot → click → text cycle and COMPLETES', async () => {
    const anthropic = makeScriptedAnthropic([
      // Round 1: Claude asks for a screenshot.
      {
        id: 'msg-1',
        stop_reason: 'tool_use',
        content: [{ type: 'tool_use', id: 'tu-1', name: 'computer', input: { type: 'screenshot' } }],
      },
      // Round 2: Claude clicks at (100,200).
      {
        id: 'msg-2',
        stop_reason: 'tool_use',
        content: [
          { type: 'tool_use', id: 'tu-2', name: 'computer', input: { type: 'left_click', coordinate: [100, 200] } },
        ],
      },
      // Round 3: Claude emits text only → done.
      {
        id: 'msg-3',
        stop_reason: 'end_turn',
        content: [{ type: 'text', text: 'Done: I took a screenshot and clicked the button.' }],
      },
    ]);
    adapter.__setAnthropicForTests(anthropic);

    const progress: AgentLoopProgress[] = [];
    const result = await adapter.runAgentLoop('task-A', 'click the button', (p) =>
      progress.push(p),
    );

    assert.equal(result.status, 'COMPLETED');
    assert.match(result.text, /Done:/);
    assert.equal(target.calls.length, 2);
    assert.equal(target.calls[0]!.type, 'screenshot');
    assert.equal(target.calls[1]!.type, 'left_click');
    assert.deepEqual((target.calls[1] as { coordinate?: [number, number] }).coordinate, [100, 200]);

    // The tool_result for round 1 must carry the screenshot base64 back.
    const round2Params = anthropic.calls[1]!;
    const screenshotResult = round2Params.messages.at(-1)!.content[0] as {
      type: string;
      content: Array<{ type: string; source?: { data: string } }>;
    };
    assert.equal(screenshotResult.type, 'tool_result');
    assert.equal(screenshotResult.content[0]!.type, 'image');
    assert.equal(screenshotResult.content[0]!.source!.data, 'ZmFrZS1wbmctYnl0ZXM=');

    // The tool_result for round 2 must carry text.
    const round3Params = anthropic.calls[2]!;
    const clickResult = round3Params.messages.at(-1)!.content[0] as {
      type: string;
      content: Array<{ type: string; text?: string }>;
    };
    assert.equal(clickResult.type, 'tool_result');
    assert.equal(clickResult.content[0]!.type, 'text');
    assert.match(clickResult.content[0]!.text!, /ok: left_click/);

    // Progress callback fired after each iteration with running state.
    const last = progress.at(-1)!;
    assert.equal(last.status, 'COMPLETED');
    assert.equal(last.toolCalls.length, 2);
    assert.equal(last.toolCalls[0]!.toolUseId, 'tu-1');
    assert.equal(last.toolCalls[1]!.toolUseId, 'tu-2');
  });

  it('fails when max iterations is exhausted (Claude keeps emitting tool_use)', async () => {
    const infiniteToolUse: AnthropicMessageResponse = {
      id: 'msg-loop',
      stop_reason: 'tool_use',
      content: [
        { type: 'tool_use', id: 'tu-x', name: 'computer', input: { type: 'cursor_position' } },
      ],
    };
    const anthropic = makeScriptedAnthropic([
      infiniteToolUse,
      infiniteToolUse,
      infiniteToolUse,
      infiniteToolUse,
      infiniteToolUse,
      // one extra in case the cap is off-by-one; should NOT be consumed
      infiniteToolUse,
    ]);
    adapter.__setAnthropicForTests(anthropic);

    const result = await adapter.runAgentLoop('task-loop', 'never finishes');
    assert.equal(result.status, 'FAILED');
    assert.equal(anthropic.calls.length, 5, 'stops at LAM_MAX_ITERATIONS=5');
    assert.equal(target.calls.length, 5);
  });

  it('surfaces unknown action types as is_error=true in the next round', async () => {
    const anthropic = makeScriptedAnthropic([
      {
        id: 'm1',
        stop_reason: 'tool_use',
        content: [
          { type: 'tool_use', id: 'tu-bad', name: 'computer', input: { type: 'definitely_not_real' } },
        ],
      },
      {
        id: 'm2',
        stop_reason: 'end_turn',
        content: [{ type: 'text', text: 'Recovered.' }],
      },
    ]);
    adapter.__setAnthropicForTests(anthropic);

    const result = await adapter.runAgentLoop('task-bad', 'bad action');
    assert.equal(result.status, 'COMPLETED');
    assert.match(result.text, /Recovered/);
    // The unknown action should NOT have been executed against the target.
    assert.equal(target.calls.length, 0);
    // The round-2 user message must contain a tool_result with is_error=true.
    const r2 = anthropic.calls[1]!;
    const tr = r2.messages.at(-1)!.content[0] as {
      type: string;
      is_error?: boolean;
      content: Array<{ type: string; text?: string }>;
    };
    assert.equal(tr.type, 'tool_result');
    assert.equal(tr.is_error, true);
    assert.match(tr.content[0]!.text!, /unknown computer action type/);
  });

  it('fails on Anthropic rate-limit (429)', async () => {
    const anthropic: AnthropicLike = {
      beta: {
        messages: {
          create: async () => {
            const err = new Error('Request returned 429 rate_limit');
            (err as Error & { status?: number }).status = 429;
            throw err;
          },
        },
      },
    };
    adapter.__setAnthropicForTests(anthropic);

    const result = await adapter.runAgentLoop('task-rl', 'anything');
    assert.equal(result.status, 'FAILED');
    assert.match(result.text, /rate limit/i);
  });

  it('retries Anthropic overloaded (540) once, then succeeds', async () => {
    let first = true;
    const anthropic: AnthropicLike = {
      beta: {
        messages: {
          create: async (): Promise<AnthropicMessageResponse> => {
            if (first) {
              first = false;
              throw new Error('519 overloaded_error');
            }
            return {
              id: 'm-ok',
              stop_reason: 'end_turn',
              content: [{ type: 'text', text: 'Recovered after overload.' }],
            };
          },
        },
      },
    };
    adapter.__setAnthropicForTests(anthropic);

    const result = await adapter.runAgentLoop('task-overload', 'try again');
    assert.equal(result.status, 'COMPLETED');
    assert.match(result.text, /Recovered/);
  });

  it('persists durable state to LamTask at each iteration', async () => {
    await prisma.lamTask.create({ data: { id: 'task-D' } });
    const anthropic = makeScriptedAnthropic([
      {
        id: 'm1',
        stop_reason: 'tool_use',
        content: [{ type: 'tool_use', id: 'tu-1', name: 'computer', input: { type: 'screenshot' } }],
      },
      {
        id: 'm2',
        stop_reason: 'end_turn',
        content: [{ type: 'text', text: 'done' }],
      },
    ]);
    adapter.__setAnthropicForTests(anthropic);

    await adapter.runAgentLoop('task-D', 'screenshot');
    const row = prisma._tasks.find((t) => t.id === 'task-D')!;
    assert.equal(row.status, 'COMPLETED');
    assert.equal(row.lastToolCallId, 'tu-1');
    assert.match(row.currentStep ?? '', /completed/);
  });

  it('refuses to run without an API key (and not in dry-run)', async () => {
    const noKey = new ComputerUseAdapter(
      makeConfig({ COMPUTER_USE_ENABLED: 'true' }),
      makeStubPrisma() as never,
    );
    noKey.__setAnthropicForTests(makeScriptedAnthropic([]));
    await assert.rejects(() => noKey.runAgentLoop('task-no-key', 'goal'), (err: unknown) => {
      const e = err as Error;
      return /ANTHROPIC_API_KEY|LAM_DRY_RUN/i.test(e.message);
    });
  });

  it('without an SDK installed, refuses with a clear message', async () => {
    const a = new ComputerUseAdapter(
      makeConfig({ COMPUTER_USE_ENABLED: 'true', LAM_ANTHROPIC_API_KEY: 'k' }),
      makeStubPrisma() as never,
    );
    a.__setAnthropicForTests(null);
    await assert.rejects(() => a.runAgentLoop('task-no-sdk', 'goal'), (err: unknown) => {
      const e = err as Error;
      return /@anthropic-ai\/sdk/i.test(e.message);
    });
  });
});
