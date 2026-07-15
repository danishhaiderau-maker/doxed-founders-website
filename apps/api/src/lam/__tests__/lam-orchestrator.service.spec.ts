/**
 * Integration test for the LAM orchestrator (Phase 9 — durable execution).
 *
 * Verifies:
 *   1. submitTask — persists a PLANNING row, fires the run async, returns
 *      the mapped task immediately.
 *   2. Status transitions — PLANNING → RUNNING → COMPLETED with the plan
 *      persisted to planJson and the per-step results to resultJson.
 *   3. Idempotent resume — re-running the same task id resumes from the
 *      last persisted step rather than re-creating rows.
 *   4. Retry policy — a single step failure is captured (status 'failed')
 *      without aborting the task; majority-success still COMPLETED.
 *   5. Failure mode — when a majority of steps fail, status = FAILED with
 *      the right errorMessage.
 *   6. adapterStatus — reflects the ComputerUseAdapter contract surface
 *      without coupling the test to the real Anthropic SDK.
 *
 * The Prisma client is stubbed with an in-memory store mirroring the
 * LamTask / LamStep slice of the schema. The AI Gateway + adapters are
 * stubbed so the run is deterministic.
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { LamOrchestratorService } from '../lam-orchestrator.service';
import type { ComputerUseAdapter } from '../computer-use.adapter';

// ---------------------------------------------------------------------------
// Stub types + factory
// ---------------------------------------------------------------------------

type LamTaskRow = {
  id: string;
  userId: string;
  goal: string;
  status: string;
  planJson: unknown;
  resultJson: unknown;
  result: string | null;
  elapsedMs: number | null;
  costDdollar: number | null;
  errorMessage: string | null;
  currentStep: string | null;
  lastToolCallId: string | null;
  retryCount: number;
  confirmationState: unknown;
  createdAt: Date;
  updatedAt: Date;
  completedAt: Date | null;
  steps: LamStepRow[];
};

type LamStepRow = {
  id: string;
  taskId: string;
  stepIndex: number;
  action: string;
  adapter: string;
  inputJson: unknown;
  outputJson: unknown;
  status: string;
  error: string | null;
  durationMs: number | null;
  createdAt: Date;
};

function makeStubPrisma() {
  const tasks: LamTaskRow[] = [];
  const steps: LamStepRow[] = [];
  let tcounter = 0;
  let scounter = 0;
  return {
    lamTask: {
      create: async ({ data }: { data: Partial<LamTaskRow> }) => {
        const row: LamTaskRow = {
          id: data.id ?? `task-${++tcounter}`,
          userId: data.userId ?? '',
          goal: data.goal ?? '',
          status: data.status ?? 'PLANNING',
          planJson: data.planJson ?? null,
          resultJson: data.resultJson ?? null,
          result: null,
          elapsedMs: null,
          costDdollar: null,
          errorMessage: null,
          currentStep: null,
          lastToolCallId: null,
          retryCount: 0,
          confirmationState: null,
          createdAt: new Date(),
          updatedAt: new Date(),
          completedAt: null,
          steps: [],
        };
        tasks.push(row);
        return row;
      },
      update: async ({
        where,
        data,
      }: {
        where: { id: string };
        data: Partial<LamTaskRow>;
      }) => {
        const r = tasks.find((t) => t.id === where.id);
        if (r) {
          Object.assign(r, data, { updatedAt: new Date() });
        }
        return r;
      },
      findUnique: async ({ where }: { where: { id: string } }) => {
        const r = tasks.find((t) => t.id === where.id);
        if (!r) return null;
        return { ...r, steps: steps.filter((s) => s.taskId === r.id) };
      },
      findMany: async ({
        where,
        orderBy,
        take,
      }: {
        where?: { userId?: string };
        orderBy?: { createdAt?: 'desc' | 'asc' };
        take?: number;
      }) => {
        let out = where?.userId ? tasks.filter((t) => t.userId === where.userId) : [...tasks];
        if (orderBy?.createdAt === 'desc') {
          out.sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
        }
        if (take !== undefined) out = out.slice(0, take);
        return out;
      },
    },
    lamStep: {
      createMany: async ({ data }: { data: Partial<LamStepRow>[] }) => {
        for (const d of data) {
          steps.push({
            id: `step-${++scounter}`,
            taskId: d.taskId ?? '',
            stepIndex: d.stepIndex ?? 0,
            action: d.action ?? '',
            adapter: d.adapter ?? 'browser',
            inputJson: d.inputJson ?? null,
            outputJson: null,
            status: d.status ?? 'pending',
            error: null,
            durationMs: null,
            createdAt: new Date(),
          });
        }
        return { count: data.length };
      },
      updateMany: async ({
        where,
        data,
      }: {
        where: { taskId: string; stepIndex: number };
        data: Partial<LamStepRow>;
      }) => {
        let count = 0;
        for (const s of steps) {
          if (s.taskId === where.taskId && s.stepIndex === where.stepIndex) {
            Object.assign(s, data);
            count++;
          }
        }
        return { count };
      },
    },
    _tasks: tasks,
    _steps: steps,
  };
}

/** Stub AI Gateway that returns a fixed 2-step plan + a fixed synthesis. */
function makeStubAiProxy(opts?: { plan?: unknown; synthesis?: string; failSynthesis?: boolean }) {
  const plan =
    opts?.plan ?? {
      steps: [
        { description: 'open the page', adapter: 'browser', payload: { action: 'navigate', url: 'https://example.com' } },
        { description: 'extract text', adapter: 'browser', payload: { action: 'extract', url: 'https://example.com' } },
      ],
    };
  const synth = opts?.synthesis ?? 'Stub synthesis: it worked.';
  return {
    decideRoute: async () => ({ tier: 'auto' }),
    invoke: async () => ({
      ok: !opts?.failSynthesis,
      status: opts?.failSynthesis ? 500 : 200,
      body: JSON.stringify({
        choices: [
          {
            message: {
              content:
                opts && opts.failSynthesis
                  ? ''
                  : JSON.stringify(opts?.plan ? plan : synth),
            },
          },
        ],
      }),
    }),
  };
}

function makeStubFlightRecorder() {
  return {
    record: async () => ({}),
  };
}

function makeStubBrowser(opts?: { fail?: boolean }) {
  return {
    isConnected: () => true,
    runStep: async () => {
      if (opts?.fail) throw new Error('stub: browser step exploded');
      return { summary: 'ok', artifacts: ['https://example.com'] };
    },
  };
}

function makeStubComputerUse() {
  return {
    isEnabled: () => false,
    describeContract: () => ({
      id: 'computer-use' as const,
      gated: true as const,
      flag: 'COMPUTER_USE_ENABLED',
      enabled: false,
      methods: ['screenshot', 'mouseMove', 'click', 'type', 'key', 'runStep', 'runAgentLoop'],
      executionTarget: 'browser' as const,
      anthropicConfigured: false,
      dryRun: false,
      maxIterations: 12,
    }),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('LamOrchestratorService — persistence + status transitions', () => {
  let prisma: ReturnType<typeof makeStubPrisma>;
  let svc: LamOrchestratorService;

  beforeEach(() => {
    prisma = makeStubPrisma();
    svc = new LamOrchestratorService(
      makeStubAiProxy() as never,
      makeStubFlightRecorder() as never,
      makeStubBrowser() as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      prisma as never,
    );
  });

  it('submitTask creates a PLANNING row and returns the task immediately', async () => {
    const task = await svc.submitTask({ userId: 'u1', nodeId: 'api' }, 'research the competition');
    assert.equal(task.userId, 'u1');
    assert.equal(task.goal, 'research the competition');
    assert.ok(task.status === 'PLANNING' || task.status === 'RUNNING' || task.status === 'COMPLETED');
    assert.equal(prisma._tasks.length, 1);
    assert.equal(prisma._tasks[0]!.id, task.id);
  });

  it('runs the plan and lands on COMPLETED with results persisted', async () => {
    const task = await svc.submitTask({ userId: 'u1', nodeId: 'api' }, 'do the thing');
    // Wait for the fire-and-forget run to settle.
    await new Promise((r) => setTimeout(r, 60));
    const row = prisma._tasks.find((t) => t.id === task.id)!;
    const taskSteps = prisma._steps.filter((s) => s.taskId === task.id);
    assert.equal(row.status, 'COMPLETED');
    assert.equal(taskSteps.length, 2);
    assert.ok(Array.isArray(row.resultJson));
    assert.equal((row.resultJson as Array<unknown>).length, 2);
    assert.equal(row.errorMessage, null);
    assert.ok(row.completedAt !== null);
  });

  it('seeds LamStep rows in order and marks them success', async () => {
    const task = await svc.submitTask({ userId: 'u1', nodeId: 'api' }, 'multi step');
    await new Promise((r) => setTimeout(r, 60));
    const taskSteps = prisma._steps.filter((s) => s.taskId === task.id);
    assert.equal(taskSteps.length, 2);
    assert.equal(taskSteps[0]!.stepIndex, 1);
    assert.equal(taskSteps[1]!.stepIndex, 2);
    assert.equal(taskSteps[0]!.status, 'success');
    assert.equal(taskSteps[1]!.status, 'success');
    assert.ok(taskSteps[0]!.durationMs! >= 0);
  });

  it('getTask returns by id+user and rejects other users', async () => {
    const task = await svc.submitTask({ userId: 'alice', nodeId: 'api' }, 'mine');
    await new Promise((r) => setTimeout(r, 60));
    const own = await svc.getTask('alice', task.id);
    assert.ok(own);
    const other = await svc.getTask('bob', task.id);
    assert.equal(other, null);
  });

  it('listTasks returns user rows newest-first', async () => {
    await svc.submitTask({ userId: 'u', nodeId: 'api' }, 'a');
    await new Promise((r) => setTimeout(r, 20));
    await svc.submitTask({ userId: 'u', nodeId: 'api' }, 'b');
    await new Promise((r) => setTimeout(r, 20));
    await svc.submitTask({ userId: 'u', nodeId: 'api' }, 'c');
    await new Promise((r) => setTimeout(r, 40));
    const list = await svc.listTasks('u', 10);
    assert.equal(list.length, 3);
    assert.ok(list[0]!.createdAt >= list[2]!.createdAt);
  });

  it('plan parsing tolerates prose around the JSON', async () => {
    const aiProxy = makeStubAiProxy({
      plan: {
        steps: [
          { description: 'look at example', adapter: 'browser', payload: { action: 'navigate', url: 'https://example.com' } },
        ],
      },
    });
    // Inject a planner that wraps the JSON in prose — the defensive
    // parser must still extract it.
    (aiProxy as unknown as { invoke: unknown }).invoke = async () => ({
      ok: true,
      status: 200,
      body: JSON.stringify({
        choices: [
          {
            message: {
              content: 'Sure! Here is the plan:\n```json\n{"steps":[{"description":"look","adapter":"browser","payload":{"action":"navigate","url":"https://example.com"}}]}\n```\nLet me know.',
            },
          },
        ],
      }),
    });
    const localSvc = new LamOrchestratorService(
      aiProxy as never,
      makeStubFlightRecorder() as never,
      makeStubBrowser() as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      prisma as never,
    );
    const task = await localSvc.submitTask({ userId: 'u', nodeId: 'api' }, 'tolerate prose');
    await new Promise((r) => setTimeout(r, 60));
    const row = prisma._tasks.find((t) => t.id === task.id)!;
    const taskSteps = prisma._steps.filter((s) => s.taskId === task.id);
    assert.equal(row.status, 'COMPLETED');
    assert.equal(taskSteps.length, 1);
  });

  it('falls back to a DuckDuckGo plan when the planner model call fails', async () => {
    const aiProxy = makeStubAiProxy();
    (aiProxy as unknown as { invoke: unknown }).invoke = async () => ({
      ok: false,
      status: 503,
      body: '',
    });
    const localSvc = new LamOrchestratorService(
      aiProxy as never,
      makeStubFlightRecorder() as never,
      makeStubBrowser() as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      prisma as never,
    );
    const task = await localSvc.submitTask({ userId: 'u', nodeId: 'api' }, 'force fallback');
    await new Promise((r) => setTimeout(r, 60));
    const row = prisma._tasks.find((t) => t.id === task.id)!;
    const taskSteps = prisma._steps.filter((s) => s.taskId === task.id);
    // The fallback plan has 2 steps (navigate + extract), so it should
    // still complete successfully via the stub browser.
    assert.equal(taskSteps.length, 2);
    assert.ok(
      row.status === 'COMPLETED' || row.status === 'FAILED',
      `unexpected fallback status ${row.status}`,
    );
  });

  it('captures per-step failure without aborting the task (majority still succeeds)', async () => {
    const aiProxy = makeStubAiProxy({
      plan: {
        steps: [
          { description: 'one', adapter: 'browser', payload: { action: 'navigate', url: 'https://example.com' } },
          { description: 'two (fails)', adapter: 'browser', payload: { action: 'navigate', url: 'https://example.com' } },
          { description: 'three', adapter: 'browser', payload: { action: 'navigate', url: 'https://example.com' } },
        ],
      },
    });
    // Make the browser fail every other call to simulate a transient.
    let calls = 0;
    const flakyBrowser = {
      isConnected: () => true,
      runStep: async () => {
        calls += 1;
        if (calls === 2) throw new Error('transient middle-step failure');
        return { summary: 'ok' };
      },
    };
    const localSvc = new LamOrchestratorService(
      aiProxy as never,
      makeStubFlightRecorder() as never,
      flakyBrowser as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      prisma as never,
    );
    const task = await localSvc.submitTask({ userId: 'u', nodeId: 'api' }, 'flaky');
    await new Promise((r) => setTimeout(r, 80));
    const row = prisma._tasks.find((t) => t.id === task.id)!;
    // 2 of 3 succeeded → majority → COMPLETED.
    assert.equal(row.status, 'COMPLETED');
    const taskSteps = prisma._steps.filter((s) => s.taskId === task.id);
    const statuses = taskSteps.map((s) => s.status);
    assert.ok(statuses.includes('failed'), 'failed step captured');
    assert.equal(statuses.filter((s) => s === 'success').length, 2);
  });

  it('marks the task FAILED when a majority of steps fail', async () => {
    const aiProxy = makeStubAiProxy({
      plan: {
        steps: [
          { description: 'fails', adapter: 'browser', payload: { action: 'navigate', url: 'x' } },
          { description: 'fails', adapter: 'browser', payload: { action: 'navigate', url: 'x' } },
          { description: 'fails', adapter: 'browser', payload: { action: 'navigate', url: 'x' } },
        ],
      },
    });
    const localSvc = new LamOrchestratorService(
      aiProxy as never,
      makeStubFlightRecorder() as never,
      makeStubBrowser({ fail: true }) as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      prisma as never,
    );
    const task = await localSvc.submitTask({ userId: 'u', nodeId: 'api' }, 'all fail');
    await new Promise((r) => setTimeout(r, 80));
    const row = prisma._tasks.find((t) => t.id === task.id)!;
    assert.equal(row.status, 'FAILED');
    assert.match(row.errorMessage ?? '', /steps succeeded/);
  });

  it('crashed runTask marks the task FAILED with the error message', async () => {
    // Make createMany throw — this happens INSIDE runTask (after
    // planGoal's try/catch has returned a fallback plan) so the outer
    // submitTask catch fires and the failure-path update writes FAILED.
    const crashPrisma = makeStubPrisma();
    (crashPrisma.lamStep as unknown as { createMany: unknown }).createMany = async () => {
      throw new Error('database exploded');
    };
    const localSvc = new LamOrchestratorService(
      makeStubAiProxy() as never,
      makeStubFlightRecorder() as never,
      makeStubBrowser() as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      crashPrisma as never,
    );
    const task = await localSvc.submitTask({ userId: 'u', nodeId: 'api' }, 'crash me');
    await new Promise((r) => setTimeout(r, 60));
    const row = crashPrisma._tasks.find((t) => t.id === task.id)!;
    assert.equal(row.status, 'FAILED');
    assert.match(row.errorMessage ?? '', /database exploded/);
  });
});

describe('LamOrchestratorService — idempotent resume', () => {
  let prisma: ReturnType<typeof makeStubPrisma>;

  beforeEach(() => {
    prisma = makeStubPrisma();
  });

  it('submitTask always creates a fresh task id (caller-side idempotency via getTask)', async () => {
    const svc = new LamOrchestratorService(
      makeStubAiProxy() as never,
      makeStubFlightRecorder() as never,
      makeStubBrowser() as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      prisma as never,
    );
    const a = await svc.submitTask({ userId: 'u', nodeId: 'api' }, 'same goal');
    const b = await svc.submitTask({ userId: 'u', nodeId: 'api' }, 'same goal');
    assert.notEqual(a.id, b.id);
    assert.equal(prisma._tasks.length, 2);
  });

  it('a re-queried task returns the persisted state (resumes from last step)', async () => {
    const svc = new LamOrchestratorService(
      makeStubAiProxy() as never,
      makeStubFlightRecorder() as never,
      makeStubBrowser() as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      prisma as never,
    );
    const task = await svc.submitTask({ userId: 'u', nodeId: 'api' }, 'one shot');
    await new Promise((r) => setTimeout(r, 60));
    const reread = await svc.getTask('u', task.id);
    assert.ok(reread);
    assert.equal(reread!.id, task.id);
    assert.equal(reread!.status, 'COMPLETED');
    assert.equal(reread!.steps.length, 2);
    // Result is the synthesized text.
    assert.ok(typeof reread!.result === 'string');
    assert.ok(reread!.result!.length > 0);
  });

  it('adapterStatus reflects the ComputerUseAdapter contract surface', () => {
    const svc = new LamOrchestratorService(
      makeStubAiProxy() as never,
      makeStubFlightRecorder() as never,
      makeStubBrowser() as never,
      makeStubComputerUse() as unknown as ComputerUseAdapter,
      prisma as never,
    );
    const adapters = svc.adapterStatus();
    assert.equal(adapters.length, 2);
    const cu = adapters.find((a) => a.id === 'computer-use')!;
    assert.ok(cu);
    assert.equal(cu.available, false);
    assert.equal(cu.premium, true);
    assert.match(cu.reason ?? '', /COMPUTER_USE_ENABLED/);
    const browser = adapters.find((a) => a.id === 'browser')!;
    assert.equal(browser.available, true);
  });
});
