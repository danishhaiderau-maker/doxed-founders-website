import {
  Injectable,
  Logger,
  NotImplementedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  WorkspaceNode,
} from '../execution-manager/execution-manager.types';
import type { ComputerUseStepPayload } from './lam.types';

/**
 * The error message a Visitor-tier founder sees when they try to use
 * Computer Use. Matches the spec in the Phase 9 brief — surfaced both
 * by the controller (tier check) and by this adapter (flag check) so
 * the gate holds at both layers.
 */
export const COMPUTER_USE_TIER_MESSAGE =
  'Computer Use is a Doxxed Builder feature. Submit your verification video to unlock.';

/**
 * ComputerUseAdapter — vision-based desktop control (premium tier).
 *
 * This is the Claude Computer Use path. The interface is implemented
 * (screenshot / mouseMove / click / type / key) so the orchestrator
 * can plan against it, but the actual Claude API call is a premium-tier
 * stub gated behind the COMPUTER_USE_ENABLED flag (default off). The
 * owner plugs in the real Claude API key later; until then, every
 * method throws a clear "premium tier not enabled" error.
 *
 * Why a stub and not an omission: the adapter needs to exist, register
 * itself, and appear in GET /api/lam/adapters so the frontend can show
 * the "Computer Use (locked)" state. Throwing on invocation keeps the
 * surface honest — nothing silently no-ops.
 *
 * Tier gate (defense in depth):
 *   1. lam.controller checks req.user.builderTier === VERIFIED_BUILDER
 *      before accepting a task that uses computer-use steps.
 *   2. This adapter re-checks COMPUTER_USE_ENABLED at call time so a
 *      task that slips past the controller (e.g. plan-time re-route)
 *      still fails closed.
 */
@Injectable()
export class ComputerUseAdapter implements ExecutionAdapter {
  readonly target = 'browser' as const; // satisfies ExecutionAdapter; computer-use is its own LAM surface
  private readonly logger = new Logger(ComputerUseAdapter.name);
  private connected = true;

  constructor(private readonly config: ConfigService) {}

  async connect(): Promise<void> {
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
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
    // TODO(phase-9-followup): wire Anthropic Claude Computer Use API.
    // Until then this throws above.
    throw this.notImplemented('screenshot');
  }

  async mouseMove(x: number, y: number): Promise<{ ok: true; x: number; y: number }> {
    this.gate();
    void x;
    void y;
    throw this.notImplemented('mouseMove');
  }

  async click(x?: number, y?: number): Promise<{ ok: true }> {
    this.gate();
    void x;
    void y;
    throw this.notImplemented('click');
  }

  async type(text: string): Promise<{ ok: true; typed: string }> {
    this.gate();
    void text;
    throw this.notImplemented('type');
  }

  async key(combo: string): Promise<{ ok: true; combo: string }> {
    this.gate();
    void combo;
    throw this.notImplemented('key');
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
  // Internals
  // -------------------------------------------------------------------------

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

  private notImplemented(op: string): NotImplementedException {
    return new NotImplementedException(
      `ComputerUseAdapter.${op}() is a premium-tier stub — ` +
        `the owner will wire the Claude Computer Use API key to enable it.`,
    );
  }
}
