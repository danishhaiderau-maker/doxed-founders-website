import { Injectable, Logger } from '@nestjs/common';
import type {
  ExecutionAction,
  ExecutionActionType,
  ExecutionAdapter,
  ExecutionResult,
  ExecutionTargetId,
  FileEdit,
  RunCommandOpts,
} from './execution-manager.types';

/**
 * Execution Manager — the kernel orchestrator (kernel service #4).
 *
 * Implements the Input → Decision → Output contract from
 * docs/PRODUCT.md §5 and docs/KERNEL.md §5:
 *
 *   Input:    an ExecutionAction (or a batch via executeGraph()).
 *   Decision: pick the adapter for the action — explicit `target`
 *             wins; otherwise fall back to the type → target map.
 *   Output:   an ExecutionResult per action, normalised so callers
 *             don't care which adapter ran.
 *
 * The Decision step is pure — it's just a registry lookup plus a
 * fallback table. The Output is observable: every action carries
 * startedAt/completedAt so the Flight Recorder can log it (the kernel
 * itself stays stateless; logging is the application's job).
 *
 * Boundary: nothing in this service imports application code. Only
 * NestJS common + Execution Manager's own types.
 */
@Injectable()
export class ExecutionManagerService {
  private readonly logger = new Logger(ExecutionManagerService.name);
  private readonly adapters = new Map<ExecutionTargetId, ExecutionAdapter>();

  /**
   * Default mapping from action type to target, used when the caller
   * doesn't pin `target` on the action. Keeps the orchestrator's
   * Decision step predictable without coupling to any adapter.
   */
  private readonly typeFallback: Record<ExecutionActionType, ExecutionTargetId> = {
    shell: 'terminal',
    'file-write': 'filesystem',
    'file-read': 'filesystem',
    'git-commit': 'terminal',
    'git-push': 'terminal',
    'browser-open': 'browser',
    http: 'terminal',
  };

  /**
   * Register an adapter under its target. Last-write-wins so test
   * harnesses can swap adapters after module init.
   */
  registerAdapter(adapter: ExecutionAdapter): void {
    this.adapters.set(adapter.target, adapter);
    this.logger.log(`registered adapter for target "${adapter.target}"`);
  }

  /**
   * Look up the adapter for a target. Throws if no adapter is
   * registered — the kernel treats unhandled targets as a wiring bug.
   */
  getAdapter(target: ExecutionTargetId): ExecutionAdapter {
    const adapter = this.adapters.get(target);
    if (!adapter) {
      throw new Error(
        `no execution adapter registered for target "${target}"`,
      );
    }
    return adapter;
  }

  /** All registered targets, for the health endpoint. */
  listTargets(): ExecutionTargetId[] {
    return Array.from(this.adapters.keys());
  }

  /** Targets whose isConnected() reports true right now. */
  listConnectedTargets(): ExecutionTargetId[] {
    return Array.from(this.adapters.values())
      .filter((a) => a.isConnected())
      .map((a) => a.target);
  }

  /**
   * Execute one action. This is the kernel's Input → Decision → Output.
   *
   *   1. INPUT     — the caller hands us an ExecutionAction.
   *   2. DECISION  — resolve the target (explicit or type→target fallback)
   *                  and pick the adapter. Pure: no side effects.
   *   3. OUTPUT    — dispatch the action to the adapter, normalise the
   *                  result into an ExecutionResult, capture timing.
   */
  async execute(action: ExecutionAction): Promise<ExecutionResult> {
    const startedAt = new Date();

    // 2. DECISION
    const target =
      action.target ?? this.resolveTargetByType(action.type);
    let adapter: ExecutionAdapter;
    try {
      adapter = this.getAdapter(target);
    } catch (err) {
      const completedAt = new Date();
      return {
        actionId: action.id,
        status: 'failed',
        stderr: err instanceof Error ? err.message : String(err),
        startedAt,
        completedAt,
      };
    }

    // 3. OUTPUT
    try {
      const result = await this.dispatch(adapter, action, target);
      return {
        actionId: action.id,
        startedAt,
        completedAt: new Date(),
        ...result,
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // NotImplementedException (Cursor stub) lands here — surface as failed.
      this.logger.warn(
        `action ${action.id} (${action.type} → ${target}) failed: ${message}`,
      );
      return {
        actionId: action.id,
        status: 'failed',
        stderr: message,
        startedAt,
        completedAt: new Date(),
      };
    }
  }

  /**
   * Execute a graph of actions in order. Short-circuits on the first
   * failure when `stopOnFailure` is true (the default) — later actions
   * are returned with status 'skipped' so callers get a result for
   * every input.
   */
  async executeGraph(
    actions: ExecutionAction[],
    opts: { stopOnFailure?: boolean } = {},
  ): Promise<ExecutionResult[]> {
    const stopOnFailure = opts.stopOnFailure ?? true;
    const results: ExecutionResult[] = [];
    let failed = false;

    for (const action of actions) {
      if (failed && stopOnFailure) {
        const now = new Date();
        results.push({
          actionId: action.id,
          status: 'skipped',
          startedAt: now,
          completedAt: now,
        });
        continue;
      }
      const result = await this.execute(action);
      results.push(result);
      if (result.status !== 'success') {
        failed = true;
      }
    }
    return results;
  }

  // -- internals ---------------------------------------------------------

  /**
   * Pure: map an action type to a default target. Doesn't touch the
   * adapter registry.
   */
  private resolveTargetByType(type: ExecutionActionType): ExecutionTargetId {
    return this.typeFallback[type];
  }

  /**
   * Hand the action to the chosen adapter, translating the action's
   * type + payload into the adapter method call. Returns a partial
   * ExecutionResult (without actionId / timing, which the caller adds).
   */
  private async dispatch(
    adapter: ExecutionAdapter,
    action: ExecutionAction,
    _target: ExecutionTargetId,
  ): Promise<Omit<ExecutionResult, 'actionId' | 'startedAt' | 'completedAt'>> {
    switch (action.type) {
      case 'shell':
      case 'git-commit':
      case 'git-push':
      case 'http': {
        const cmdPayload = action.payload as { command: string };
        const command = cmdPayload?.command;
        if (typeof command !== 'string' || command.length === 0) {
          return {
            status: 'failed',
            stderr: `action ${action.type} requires payload.command (string)`,
          };
        }
        const opts: RunCommandOpts = {
          cwd: action.cwd,
          timeoutMs: action.timeoutMs,
        };
        const cr = await adapter.runCommand(command, opts);
        const status: ExecutionResult['status'] =
          cr.signal === 'timeout'
            ? 'timeout'
            : cr.exitCode === 0
              ? 'success'
              : 'failed';
        return {
          status,
          stdout: cr.stdout,
          stderr: cr.stderr,
          exitCode: cr.exitCode,
        };
      }

      case 'file-write': {
        const edits = this.coerceEdits(action.payload);
        if (edits.length === 0) {
          return {
            status: 'failed',
            stderr: 'action file-write requires payload.edits (FileEdit[])',
          };
        }
        const outcomes = await adapter.applyEdits(edits);
        const anyFailed = outcomes.some((o) => !o.ok);
        return {
          status: anyFailed ? 'failed' : 'success',
          stdout: JSON.stringify(outcomes),
          artifacts: outcomes.filter((o) => o.ok).map((o) => o.path),
        };
      }

      case 'file-read': {
        const readPayload = action.payload as { path?: string };
        const nodes = await adapter.readWorkspace(readPayload?.path ?? action.cwd);
        return {
          status: 'success',
          stdout: JSON.stringify(nodes),
          artifacts: nodes.map((n) => n.path),
        };
      }

      case 'browser-open': {
        // The browser adapter is a stub for now. If we ever route here
        // without one registered, execute() will have already failed
        // at getAdapter(). If a browser adapter IS registered later,
        // we still need its specific call surface — for now, run it
        // as a shell command to the OS open helper.
        const urlPayload = action.payload as { url?: string };
        const url = urlPayload?.url;
        if (typeof url !== 'string') {
          return {
            status: 'failed',
            stderr: 'action browser-open requires payload.url (string)',
          };
        }
        return {
          status: 'skipped',
          stderr: 'browser-open not wired yet (no browser adapter)',
          artifacts: [url],
        };
      }

      default: {
        // Exhaustiveness check — if a new action type is added to
        // ExecutionActionType without a case here, this branch catches it.
        const _exhaustive: never = action.type;
        void _exhaustive;
        return {
          status: 'failed',
          stderr: `unknown action type`,
        };
      }
    }
  }

  /**
   * Narrow an unknown payload to a FileEdit[] for file-write actions.
   * Lenient about extra fields, strict about the required ones.
   */
  private coerceEdits(payload: unknown): FileEdit[] {
    if (!payload || typeof payload !== 'object') return [];
    const maybe = payload as { edits?: unknown };
    if (!Array.isArray(maybe.edits)) return [];
    const out: FileEdit[] = [];
    for (const raw of maybe.edits) {
      if (!raw || typeof raw !== 'object') continue;
      const e = raw as Partial<FileEdit>;
      if (
        typeof e.path === 'string' &&
        typeof e.content === 'string' &&
        (e.kind === 'create' ||
          e.kind === 'overwrite' ||
          e.kind === 'append' ||
          e.kind === 'patch')
      ) {
        out.push({
          path: e.path,
          content: e.content,
          kind: e.kind,
          anchor: typeof e.anchor === 'string' ? e.anchor : undefined,
        });
      }
    }
    return out;
  }
}
