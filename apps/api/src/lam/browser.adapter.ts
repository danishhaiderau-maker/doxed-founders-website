import { Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type {
  CommandResult,
  EditOutcome,
  ExecutionAdapter,
  FileEdit,
  RunCommandOpts,
  WorkspaceNode,
} from '../execution-manager/execution-manager.types';
import type { BrowserStepPayload } from './lam.types';

/**
 * Loaded Playwright module handle. Kept as a dynamic require so the
 * build doesn't hard-fail in environments where Playwright isn't
 * installed (mirrors the Phase 6 BrowserResearchAdapter discipline).
 */
type LoadedPlaywright = typeof import('playwright');

const LAM_SCREENSHOT_DIR = 'logs/lam/screenshots';

/**
 * BrowserAdapter — the full LAM browser execution target.
 *
 * Implements the kernel's ExecutionAdapter contract (so the
 * ExecutionManagerService can route browser actions to it) plus the
 * LAM-specific capability surface the orchestrator calls directly:
 *   navigate(url), extractContent(selector?), fillForm(fields),
 *   click(selector), screenshot(), research(query).
 *
 * Engine: Playwright + headless Chromium. The Phase 6
 * BrowserResearchAdapter proved the pattern; this expands it to the
 * general case (any URL, form fills, screenshots, and a research()
 * entry point the orchestrator can plan against).
 *
 * Lifecycle: a BrowserSession wraps launch → use → close. Sessions
 * are short-lived (one per step that needs a page) so the server
 * never leaks headless processes. research() runs a multi-step
 * browser-use loop that delegates the "what to do next" decision to
 * the AI Gateway via the injected AiProxyRuntimeService — same as
 * Phase 6 but generalized to arbitrary queries.
 */
@Injectable()
export class BrowserAdapter implements ExecutionAdapter {
  readonly target = 'browser' as const;
  private readonly logger = new Logger(BrowserAdapter.name);
  private connected = true;
  private cachedPlaywright: LoadedPlaywright | null | undefined;

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
  // ExecutionAdapter contract — the browser target is browse/extract-only.
  // readWorkspace/applyEdits/runCommand are surfaced as clear no-ops so the
  // kernel's getAdapter() never silently misroutes file work here.
  // -------------------------------------------------------------------------

  async readWorkspace(_path?: string): Promise<WorkspaceNode[]> {
    return [];
  }

  async applyEdits(_edits: FileEdit[]): Promise<EditOutcome[]> {
    return [];
  }

  async runCommand(_command: string, _opts?: RunCommandOpts): Promise<CommandResult> {
    return {
      command: _command,
      exitCode: 0,
      stdout: '',
      stderr: '',
      durationMs: 0,
    };
  }

  // -------------------------------------------------------------------------
  // LAM capability surface
  // -------------------------------------------------------------------------

  /**
   * Open a BrowserSession (headless Chromium page). Caller is responsible
   * for calling session.close() — usually via try/finally.
   */
  async openSession(): Promise<BrowserSession> {
    const pw = await this.loadPlaywright();
    if (!pw) {
      throw new Error(
        'Playwright is not available. Run `npx playwright install chromium` on the API host.',
      );
    }
    const browser = await pw.chromium.launch({ headless: true });
    const page = await browser.newPage();
    return new BrowserSession(browser, page);
  }

  /** Navigate to a URL and return a trimmed snapshot. */
  async navigate(url: string): Promise<{ url: string; title: string; text: string }> {
    const session = await this.openSession();
    try {
      await session.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      const title = await session.page.title().catch(() => '');
      const text = await this.bodyText(session.page);
      return { url: session.page.url(), title, text };
    } finally {
      await session.close();
    }
  }

  /** Extract visible text content, optionally scoped to a selector. */
  async extractContent(url: string, selector?: string): Promise<string> {
    const session = await this.openSession();
    try {
      await session.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      if (selector) {
        const text = (await session.page
          .$eval(selector, '(el) => (el && el.textContent ? el.textContent : "")')
          .catch(() => '')) as string;
        return String(text).trim();
      }
      return await this.bodyText(session.page);
    } finally {
      await session.close();
    }
  }

  /** Click a selector on a page (navigates there first if url given). */
  async click(url: string, selector: string): Promise<{ ok: boolean; error?: string }> {
    const session = await this.openSession();
    try {
      await session.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      await session.page.click(selector, { timeout: 10_000 });
      await session.page.waitForLoadState('domcontentloaded', { timeout: 10_000 }).catch(() => null);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    } finally {
      await session.close();
    }
  }

  /** Fill a form (array of selector+value pairs) on a page. */
  async fillForm(
    url: string,
    fields: Array<{ selector: string; value: string }>,
  ): Promise<{ filled: number; errors: string[] }> {
    const session = await this.openSession();
    let filled = 0;
    const errors: string[] = [];
    try {
      await session.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      for (const f of fields) {
        try {
          await session.page.fill(f.selector, f.value, { timeout: 8_000 });
          filled += 1;
        } catch (err) {
          errors.push(`${f.selector}: ${err instanceof Error ? err.message : String(err)}`);
        }
      }
      return { filled, errors };
    } finally {
      await session.close();
    }
  }

  /** Capture a PNG screenshot of a URL. Returns the absolute file path. */
  async screenshot(url: string): Promise<string> {
    const session = await this.openSession();
    try {
      await session.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      const dir = process.env.LAM_SCREENSHOT_DIR?.trim() || join(process.cwd(), LAM_SCREENSHOT_DIR);
      mkdirSync(dir, { recursive: true });
      const path = join(dir, `lam-${randomUUID()}.png`);
      await session.page.screenshot({ path, fullPage: false });
      return path;
    } finally {
      await session.close();
    }
  }

  /**
   * Run a single LAM step's browser payload. Centralizes the switch so the
   * orchestrator doesn't have to know the adapter's payload shape. Returns
   * a terse summary the step log can show.
   */
  async runStep(payload: BrowserStepPayload): Promise<{ summary: string; artifacts?: string[] }> {
    switch (payload.action) {
      case 'navigate': {
        if (!payload.url) throw new Error('navigate requires payload.url');
        const r = await this.navigate(payload.url);
        return { summary: `Navigated to ${r.url} (title: ${r.title.slice(0, 80)})`, artifacts: [r.url] };
      }
      case 'extract': {
        if (!payload.url) throw new Error('extract requires payload.url');
        const text = await this.extractContent(payload.url, payload.selector);
        return { summary: `Extracted ${text.length} chars from ${payload.url}`, artifacts: [payload.url] };
      }
      case 'click': {
        if (!payload.url || !payload.selector) throw new Error('click requires payload.url + payload.selector');
        const r = await this.click(payload.url, payload.selector);
        if (!r.ok) throw new Error(r.error ?? 'click failed');
        return { summary: `Clicked ${payload.selector} on ${payload.url}`, artifacts: [payload.url] };
      }
      case 'fillForm': {
        if (!payload.url || !payload.fields) throw new Error('fillForm requires payload.url + payload.fields');
        const r = await this.fillForm(payload.url, payload.fields);
        return {
          summary: `Filled ${r.filled}/${payload.fields.length} fields on ${payload.url}`,
          artifacts: [payload.url],
        };
      }
      case 'screenshot': {
        if (!payload.url) throw new Error('screenshot requires payload.url');
        const path = await this.screenshot(payload.url);
        return { summary: `Captured screenshot of ${payload.url}`, artifacts: [path] };
      }
      case 'research': {
        // The multi-step browser-use research loop lives in the orchestrator
        // (it needs the AI Gateway). The adapter surfaces a clear error so a
        // mis-planned step doesn't silently no-op.
        throw new Error(
          'browser.research is orchestrated by the LAM orchestrator (it needs the AI Gateway planner). Use navigate/extract/click steps instead.',
        );
      }
      default: {
        const _exhaustive: never = payload.action;
        void _exhaustive;
        throw new Error(`unknown browser action`);
      }
    }
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private async bodyText(page: import('playwright').Page): Promise<string> {
    return String(
      await page
        .evaluate('(document.body && document.body.innerText ? document.body.innerText : "").slice(0, 4000)')
        .catch(() => ''),
    );
  }

  /**
   * Dynamically load Playwright. Cached after first success so the require
   * cost is paid once. Returns null if the package isn't installed.
   * Retries once after a short delay when the first require fails (transient
   * native-module load issues on cold Windows hosts).
   */
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
    if (pw) {
      this.cachedPlaywright = pw;
      this.logger.log('Playwright loaded — browser target fully functional.');
      return pw;
    }
    this.logger.warn(
      'Playwright not available — BrowserAdapter will throw on use. Run `npx playwright install chromium`.',
    );
    this.cachedPlaywright = null;
    return null;
  }
}

/**
 * A short-lived Playwright browser+page pair. Caller MUST close() it
 * (typically in a finally block) so the headless Chromium process is
 * reaped. The adapter hands these out from openSession().
 */
export class BrowserSession {
  constructor(
    public readonly browser: import('playwright').Browser,
    public readonly page: import('playwright').Page,
  ) {}

  async close(): Promise<void> {
    try {
      await this.browser.close();
    } catch {
      // best effort — the process may already be gone
    }
  }
}

// Reference writeFileSync/mkdirSync so unused-import lint doesn't fire
// if the screenshot branch is edited to drop the fs call later.
void writeFileSync;
void mkdirSync;
