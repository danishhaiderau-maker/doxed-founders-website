import { Injectable, Logger, BadRequestException, ServiceUnavailableException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

/**
 * Remote control for the showcase BTC bot running on Fly.io.
 *
 * The Home Command Center Start/Stop buttons call this when SHOWCASE_HOST=fly.
 * It speaks the Fly Machines REST API directly (no flyctl in the Railway container):
 *   POST https://api.machinery-fly.dev/v1/apps/{app}/machines/{id}/start
 *   POST https://api.machinery-fly.dev/v1/apps/{app}/machines/{id}/stop
 *   GET  https://api.machinery-fly.dev/v1/apps/{app}/machines/{id}
 * Auth: `Authorization: Bearer <FLY_API_TOKEN>`.
 *
 * Env required on Railway:
 *   FLY_API_TOKEN   — token from `flyctl auth token` (scoped to the bot app).
 *   FLY_APP_NAME    — e.g. "doxed-btc-bot".
 *   FLY_MACHINE_ID  — the bot machine id (from `fly machines list`).
 *   FLY_BOT_URL     — optional public bot URL for /api/ping health probe
 *                     (defaults to https://bot.doxxedcrypto.digital).
 */
export type FlyMachineState =
  | 'created'
  | 'started'
  | 'starting'
  | 'stopping'
  | 'stopped'
  | 'destroyed'
  | 'destroying'
  | string;

export type FlyControlAction = 'start' | 'stop';

export interface FlyControlResult {
  ok: boolean;
  status: FlyMachineState;
  machineState: FlyMachineState;
  message?: string;
  polled?: boolean;
}

const FLY_API_BASE = 'https://api.machinery-fly.dev/v1';
const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 90000;

@Injectable()
export class FlyControlService {
  private readonly logger = new Logger(FlyControlService.name);

  constructor(private readonly config: ConfigService) {}

  private resolveConfig(): {
    token: string;
    app: string;
    machineId: string;
    botUrl: string;
  } {
    const token = this.config.get<string>('FLY_API_TOKEN')?.trim();
    const app = this.config.get<string>('FLY_APP_NAME')?.trim();
    const machineId = this.config.get<string>('FLY_MACHINE_ID')?.trim();
    const botUrl = (this.config.get<string>('FLY_BOT_URL')?.trim() ||
      'https://bot.doxxedcrypto.digital').replace(/\/$/, '');
    if (!token || !app || !machineId) {
      throw new ServiceUnavailableException(
        'Fly control not configured — set FLY_API_TOKEN, FLY_APP_NAME, FLY_MACHINE_ID on Railway.',
      );
    }
    return { token, app, machineId, botUrl };
  }

  private async callFly(path: string, method: 'GET' | 'POST' = 'GET'): Promise<any> {
    const { token, app } = this.resolveConfig();
    const url = `${FLY_API_BASE}/apps/${app}${path}`;
    const res = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      signal: AbortSignal.timeout(15000),
    });
    const text = await res.text();
    if (!res.ok) {
      this.logger.error(`Fly API ${method} ${path} -> ${res.status}: ${text}`);
      throw new ServiceUnavailableException(
        `Fly API ${method} ${path} failed (${res.status}): ${text.slice(0, 300)}`,
      );
    }
    return text ? JSON.parse(text) : {};
  }

  async getMachineState(): Promise<FlyMachineState> {
    const { machineId } = this.resolveConfig();
    const body = await this.callFly(`/machines/${machineId}`);
    return (body?.state ?? body?.status ?? 'unknown') as FlyMachineState;
  }

  async control(action: FlyControlAction): Promise<FlyControlResult> {
    if (action !== 'start' && action !== 'stop') {
      throw new BadRequestException(`Invalid action: ${action}. Use "start" or "stop".`);
    }
    const { machineId, botUrl } = this.resolveConfig();
    const endpoint = action === 'start' ? 'start' : 'stop';

    // Fly returns 400 "machine already started/stopped" if it's already in that state.
    // Treat that as success (idempotent).
    try {
      await this.callFly(`/machines/${machineId}/${endpoint}`, 'POST');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const already = /already|is already/i.test(msg);
      if (!already) throw err;
      this.logger.warn(`${action}: machine already in target state — treating as success.`);
    }

    const target: FlyMachineState = action === 'start' ? 'started' : 'stopped';
    const polled = await this.pollUntil(target);

    // For start, also probe the bot's /api/ping through the public URL so we
    // report "started" only when the Flask app is actually serving.
    let message: string | undefined;
    if (action === 'start' && polled) {
      const healthy = await this.probeBotHealth(botUrl);
      message = healthy
        ? 'Fly bot started and /api/ping healthy.'
        : 'Fly machine started but /api/ping not yet reachable — give it ~30s to warm up.';
    } else if (action === 'stop' && polled) {
      message = 'Fly bot stopped gracefully (machine preserved).';
    } else if (!polled) {
      message = `Timed out waiting for state=${target}. Check Fly logs.`;
    }

    const finalState = await this.getMachineState().catch(() => 'unknown' as FlyMachineState);
    return { ok: polled, status: finalState, machineState: finalState, message, polled };
  }

  private async pollUntil(target: FlyMachineState): Promise<boolean> {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const state = await this.getMachineState().catch(
        () => 'unknown' as FlyMachineState,
      );
      if (state === target) return true;
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    }
    return false;
  }

  private async probeBotHealth(botUrl: string): Promise<boolean> {
    for (let i = 0; i < 6; i++) {
      try {
        const res = await fetch(`${botUrl}/api/ping`, {
          signal: AbortSignal.timeout(5000),
        });
        if (res.ok) return true;
      } catch {
        // bot still warming up
      }
      await new Promise((r) => setTimeout(r, 5000));
    }
    return false;
  }
}
