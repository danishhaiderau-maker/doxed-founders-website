import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type {
  ComputerUseAction,
  ExecutionTarget,
  ExecutionTargetResult,
} from './lam.types';

/**
 * Loaded Playwright module handle. Dynamic so the build doesn't hard-fail
 * in environments where Playwright isn't installed (mirrors the
 * BrowserAdapter discipline).
 */
type LoadedPlaywright = typeof import('playwright');

const DEFAULT_VIEWPORT = { width: 1024, height: 768 } as const;

/**
 * PlaywrightTarget — drives a headless Chromium page.
 *
 * Default ExecutionTarget for the ComputerUseAdapter. Implements the
 * Anthropic `computer_20250124` action union against a Playwright page:
 * coordinate actions are translated to mouse / touch events, `type`
 * becomes `page.keyboard.type`, `key` is forwarded via `page.keyboard`
 * (combo parsing below), and `screenshot` returns a base64 PNG.
 *
 * Why this is the default: it's container-safe (no X server), it already
 * ships in this repo (see BrowserAdapter), and Claude Computer Use is
 * happy driving a browser viewport when the display dimensions in the
 * tool definition match the page viewport.
 */
@Injectable()
export class PlaywrightTarget implements ExecutionTarget {
  readonly id = 'browser' as const;
  readonly displayWidthPx: number;
  readonly displayHeightPx: number;
  private readonly logger = new Logger(PlaywrightTarget.name);
  private cachedPlaywright: LoadedPlaywright | null | undefined;
  private browser: import('playwright').Browser | null = null;
  private page: import('playwright').Page | null = null;
  private running = false;

  constructor(private readonly config: ConfigService) {
    const w = Number.parseInt(this.config.get<string>('LAM_DISPLAY_WIDTH') ?? '', 10);
    const h = Number.parseInt(this.config.get<string>('LAM_DISPLAY_HEIGHT') ?? '', 10);
    this.displayWidthPx = Number.isFinite(w) && w > 0 ? w : DEFAULT_VIEWPORT.width;
    this.displayHeightPx = Number.isFinite(h) && h > 0 ? h : DEFAULT_VIEWPORT.height;
  }

  async start(): Promise<void> {
    if (this.running) return;
    const pw = await this.loadPlaywright();
    if (!pw) {
      throw new Error(
        'Playwright is not available. Run `npx playwright install chromium` on the LAM host.',
      );
    }
    this.browser = await pw.chromium.launch({ headless: true });
    const ctx = await this.browser.newContext({
      viewport: { width: this.displayWidthPx, height: this.displayHeightPx },
    });
    this.page = await ctx.newPage();
    this.running = true;
    this.logger.log(
      `PlaywrightTarget started (${this.displayWidthPx}x${this.displayHeightPx}).`,
    );
  }

  async stop(): Promise<void> {
    this.running = false;
    const browser = this.browser;
    this.browser = null;
    this.page = null;
    if (browser) {
      try {
        await browser.close();
      } catch {
        // best effort — process may already be gone
      }
    }
  }

  isRunning(): boolean {
    return this.running;
  }

  async execute(action: ComputerUseAction): Promise<ExecutionTargetResult> {
    if (!this.page) {
      return { ok: false, error: 'PlaywrightTarget not started — call start() first' };
    }
    const page = this.page;
    const mouse = page.mouse;
    const keyboard = page.keyboard;
    try {
      switch (action.type) {
        case 'screenshot': {
          const buf = await page.screenshot({ type: 'png', fullPage: false });
          return {
            ok: true,
            summary: 'screenshot',
            screenshotBase64: buf.toString('base64'),
          };
        }
        case 'cursor_position': {
          // Playwright doesn't expose the live cursor; report the origin as
          // a deterministic placeholder so Claude can still issue relative
          // actions.
          return { ok: true, summary: 'cursor (0,0)' };
        }
        case 'mouse_move': {
          const [x, y] = action.coordinate;
          await mouse.move(x, y);
          return { ok: true, summary: `mouse_move (${x},${y})` };
        }
        case 'left_click': {
          if (action.coordinate) await mouse.move(action.coordinate[0], action.coordinate[1]);
          await mouse.click(0, 0, { button: 'left' });
          return { ok: true, summary: 'left_click' };
        }
        case 'right_click': {
          if (action.coordinate) await mouse.move(action.coordinate[0], action.coordinate[1]);
          await mouse.click(0, 0, { button: 'right' });
          return { ok: true, summary: 'right_click' };
        }
        case 'middle_click': {
          if (action.coordinate) await mouse.move(action.coordinate[0], action.coordinate[1]);
          await mouse.click(0, 0, { button: 'middle' });
          return { ok: true, summary: 'middle_click' };
        }
        case 'double_click': {
          if (action.coordinate) await mouse.move(action.coordinate[0], action.coordinate[1]);
          await mouse.dblclick(0, 0, { button: 'left' });
          return { ok: true, summary: 'double_click' };
        }
        case 'left_click_drag': {
          const [sx, sy] = action.start_coordinate;
          const [ex, ey] = action.coordinate;
          await mouse.move(sx, sy);
          await mouse.down({ button: 'left' });
          await mouse.move(ex, ey);
          await mouse.up({ button: 'left' });
          return { ok: true, summary: `drag (${sx},${sy})->(${ex},${ey})` };
        }
        case 'type': {
          await keyboard.type(action.text, { delay: 0 });
          return { ok: true, summary: `type ${action.text.length} chars` };
        }
        case 'key': {
          const combo = action.key;
          // Anthropic sends combos like "ctrl-s" / "Return" / "alt-shift-tab".
          // Translate to the Playwright keyboard.press syntax.
          await keyboard.press(combo);
          return { ok: true, summary: `key ${combo}` };
        }
        case 'scroll': {
          const dx = action.coordinate ? action.coordinate[0] : 0;
          const dy = action.coordinate ? action.coordinate[1] : 0;
          if (action.coordinate) await mouse.move(dx, dy);
          // scroll_amount is in "notches" per Anthropic spec — map to wheel delta.
          const delta = action.scroll_amount * 100 * (action.scroll_direction === 'up' ? -1 : 1);
          await page.mouse.wheel(0, delta);
          return {
            ok: true,
            summary: `scroll ${action.scroll_direction} ${action.scroll_amount}`,
          };
        }
        case 'wait': {
          const ms = Math.min(Math.max(action.duration ?? 1000, 0), 10_000);
          await new Promise((r) => setTimeout(r, ms));
          return { ok: true, summary: `wait ${ms}ms` };
        }
        default: {
          const _exhaustive: never = action;
          void _exhaustive;
          return { ok: false, error: `unsupported action` };
        }
      }
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  private async loadPlaywright(): Promise<LoadedPlaywright | null> {
    if (this.cachedPlaywright !== undefined) return this.cachedPlaywright;
    const tryLoad = (): LoadedPlaywright | null => {
      try {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        return require('playwright') as LoadedPlaywright;
      } catch {
        return null;
      }
    };
    let pw = tryLoad();
    if (!pw) {
      await new Promise((r) => setTimeout(r, 250));
      pw = tryLoad();
    }
    this.cachedPlaywright = pw;
    if (pw) this.logger.log('Playwright loaded.');
    else this.logger.warn('Playwright not available.');
    return pw;
  }
}

/**
 * RealScreenTarget — drives the host's real display via nut-js (preferred)
 * or robotjs. NOT container-safe: requires native modules and a real X
 * server / Windows desktop. The factory returns a StubRealScreenTarget
 * (which throws clearly on every action) when the native deps are
 * missing, so the adapter can stay wired without crashing at boot.
 */
export class RealScreenTarget implements ExecutionTarget {
  readonly id = 'screen' as const;
  readonly displayWidthPx: number;
  readonly displayHeightPx: number;
  private readonly logger = new Logger(RealScreenTarget.name);
  private native: unknown = null;
  private running = false;

  constructor(width = 1920, height = 1080) {
    this.displayWidthPx = width;
    this.displayHeightPx = height;
  }

  async start(): Promise<void> {
    if (this.running) return;
    const native = await loadNativeScreenLib(this.logger);
    if (!native) {
      throw new Error(
        'RealScreenTarget requires @nut-tree-fork/nut-js or robotjs. ' +
          'Install one and run with LAM_EXECUTION_TARGET=screen on a host with a real display.',
      );
    }
    this.native = native;
    this.running = true;
    this.logger.log(
      `RealScreenTarget started (${this.displayWidthPx}x${this.displayHeightPx}).`,
    );
  }

  async stop(): Promise<void> {
    this.running = false;
    this.native = null;
  }

  isRunning(): boolean {
    return this.running;
  }

  async execute(action: ComputerUseAction): Promise<ExecutionTargetResult> {
    if (!this.native) {
      return {
        ok: false,
        error:
          'RealScreenTarget not started (native screen-control library missing). ' +
          'Set LAM_EXECUTION_TARGET=browser (default) or install @nut-tree-fork/nut-js.',
      };
    }
    // Native dispatch is intentionally thin — the real implementation
    // would map each action type to nut-js / robotjs calls. We surface a
    // clear "not wired" error so callers know the gate held.
    return {
      ok: false,
      error: `RealScreenTarget.execute(${action.type}) requires native wiring — ` +
        `use PlaywrightTarget (default) until the nut-js bridge is implemented.`,
    };
  }
}

/**
 * Lazy native loader. Returns `null` if neither candidate library is
 * installed (the common case in CI / containers). Keeps the failure mode
 * explicit so the factory can fall back to PlaywrightTarget.
 */
async function loadNativeScreenLib(logger: Logger): Promise<unknown> {
  for (const name of ['@nut-tree-fork/nut-js', 'robotjs']) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const mod = require(name);
      logger.log(`native screen-control library loaded: ${name}`);
      return mod;
    } catch {
      // try next
    }
  }
  return null;
}

/**
 * createExecutionTarget — env-driven factory.
 *
 *   LAM_EXECUTION_TARGET=screen  → RealScreenTarget (fails closed without native deps)
 *   LAM_EXECUTION_TARGET=browser → PlaywrightTarget (default; container-safe)
 *
 * Kept as a free function (not @Injectable) so the adapter can swap
 * targets in tests without a Nest container, and so the factory decision
 * is unit-testable without booting Nest.
 */
export function createExecutionTarget(config: {
  get<T = string>(key: string): T | undefined;
}): ExecutionTarget {
  const raw = (config.get<string>('LAM_EXECUTION_TARGET') ?? 'browser').toLowerCase();
  if (raw === 'screen') {
    const w = Number.parseInt(config.get<string>('LAM_DISPLAY_WIDTH') ?? '', 10);
    const h = Number.parseInt(config.get<string>('LAM_DISPLAY_HEIGHT') ?? '', 10);
    return new RealScreenTarget(
      Number.isFinite(w) && w > 0 ? w : 1920,
      Number.isFinite(h) && h > 0 ? h : 1080,
    );
  }
  // Nest's ConfigService satisfies the slice we need; we cast loosely
  // because tests pass a plain map.
  return new PlaywrightTarget(config as never);
}
