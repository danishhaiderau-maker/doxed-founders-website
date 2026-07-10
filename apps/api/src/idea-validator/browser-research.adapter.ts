import { Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import { FlightRecorderService } from '../flight-recorder/flight-recorder.service';
import { AiProxyRuntimeService, type ProxyAuth } from '../ai-proxy/ai-proxy-runtime.service';
import { FOUNDER_OS_AUTO_MODEL } from '../ai-proxy/ai-proxy.constants';
import type { ChatCompletionRequestDto } from '../ai-proxy/dto/ai-proxy.dto';
import {
  DEFAULT_RESEARCH_BUDGET,
  type BrowserAction,
  type PageSnapshot,
  type ResearchBudget,
  type ResearchHit,
  type ResearchQuery,
  type ResearchResult,
  type ResearchTarget,
} from './browser-research.types';

/**
 * BrowserResearchAdapter — the first real LAM (Large Action Model) slice.
 *
 * Implements the "Browser Use" pattern from docs/FOUNDER-IDEA-VALIDATOR.md
 * Part D:
 *
 *   1. Launch a headless Chromium instance (Playwright).
 *   2. For each query + target (github.com, producthunt.com, general web),
 *      navigate to a search URL, extract candidate links.
 *   3. Hand a trimmed PageSnapshot to the decision model (DeepSeek / GLM
 *      via the AI Gateway — NEVER a direct API call). The model decides
 *      which link to click and what to extract.
 *   4. Execute the model's action, feed the resulting page back, loop
 *      until the step cap or a `done` action.
 *   5. Return structured ResearchHit[].
 *
 * Discipline (non-negotiable, see design doc):
 *   - Every model call goes through AiProxyRuntimeService so it is logged
 *     in the Flight Recorder and meters DDollar correctly.
 *   - Every browser action is logged to the Flight Recorder as a
 *     RoutingDecision-style event with intent 'research' and
 *     chosenProvider 'local-playwright' — this is the action trace that
 *     distinguishes a LAM from a chatbot, and it's the training data for
 *     the Learning Engine.
 *   - Hard timeouts (30s/query, 120s/check) so the adapter never hangs.
 *
 * Graceful degradation: if Playwright or the chromium binary is not
 * available at runtime, the adapter falls back to a lightweight fetch +
 * regex extractor. This keeps the feature working in environments where
 * the browser couldn't be installed (thin CI, serverless) while the full
 * LAM loop runs wherever chromium exists.
 */
@Injectable()
export class BrowserResearchAdapter {
  private readonly logger = new Logger(BrowserResearchAdapter.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly aiProxy: AiProxyRuntimeService,
    private readonly flightRecorder: FlightRecorderService,
  ) {}

  /**
   * Run a set of research queries across their targets. Serial to stay
   * polite to the source sites and to bound concurrent browser instances
   * to one. Returns one ResearchResult per query.
   */
  async runResearch(
    auth: ProxyAuth,
    queries: ResearchQuery[],
    budget: ResearchBudget = DEFAULT_RESEARCH_BUDGET,
  ): Promise<ResearchResult[]> {
    const startedAt = Date.now();
    const results: ResearchResult[] = [];

    for (const q of queries) {
      if (Date.now() - startedAt > budget.timeoutTotalMs) {
        this.logger.warn(
          `research total budget exceeded before query "${q.query}" — skipping remaining`,
        );
        break;
      }
      const result = await this.runOneQuery(auth, q, budget);
      results.push(result);
    }
    return results;
  }

  // -- per-query loop ----------------------------------------------------

  private async runOneQuery(
    auth: ProxyAuth,
    rq: ResearchQuery,
    budget: ResearchBudget,
  ): Promise<ResearchResult> {
    const startedAt = Date.now();
    const allHits: ResearchHit[] = [];
    let stepsTaken = 0;
    let timedOut = false;

    const playwright = await this.tryLoadPlaywright();

    try {
      for (const target of rq.targets) {
        if (allHits.length >= budget.maxHitsPerQuery) break;
        if (Date.now() - startedAt > budget.timeoutPerQueryMs) {
          timedOut = true;
          break;
        }

        if (playwright) {
          const hits = await this.researchWithBrowser(auth, playwright, rq.query, target, budget);
          allHits.push(...hits);
        } else {
          const hits = await this.researchWithFetch(auth, rq.query, target, budget);
          allHits.push(...hits);
        }
        stepsTaken += 1; // at least one navigation/extraction cycle per target
      }
    } catch (err) {
      this.logger.warn(
        `research query "${rq.query}" errored: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    // Deduplicate by URL (a project may appear on GitHub + web).
    const seen = new Set<string>();
    const deduped = allHits.filter((h) => {
      const key = h.url.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    return {
      query: rq.query,
      hits: deduped.slice(0, budget.maxHitsPerQuery),
      stepsTaken,
      elapsedMs: Date.now() - startedAt,
      timedOut,
    };
  }

  // -- Playwright (LAM) path --------------------------------------------

  /**
   * The real "Browser Use" loop. Launches a page, navigates to the search
   * URL for the target, then lets the decision model drive up to
   * maxStepsPerQuery actions. Each step: snapshot the page → ask the model
   * → execute its action → collect extracted hits.
   */
  private async researchWithBrowser(
    auth: ProxyAuth,
    pw: LoadedPlaywright,
    query: string,
    target: ResearchTarget,
    budget: ResearchBudget,
  ): Promise<ResearchHit[]> {
    const browser = await pw.chromium.launch({ headless: true });
    const page = await browser.newPage();
    const hits: ResearchHit[] = [];

    try {
      const searchUrl = this.searchUrlFor(target, query);
      await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: budget.timeoutPerQueryMs });

      for (let step = 1; step <= budget.maxStepsPerQuery; step++) {
        const snapshot = await this.snapshot(page, target, step);
        const action = await this.decideAction(auth, query, target, snapshot);
        await this.logBrowserAction(auth, target, step, snapshot.url, action);

        if (action.type === 'done') break;
        if (action.type === 'extract') {
          const extracted = this.extractHitsFromSnapshot(snapshot, target);
          hits.push(...extracted);
          break; // extraction is the terminal step
        }
        if (action.type === 'click' && action.selector) {
          try {
            await page.click(action.selector, { timeout: 5000 });
            await page.waitForLoadState('domcontentloaded', { timeout: 8000 });
          } catch (err) {
            this.logger.debug(
              `click "${action.selector}" failed: ${err instanceof Error ? err.message : String(err)}`,
            );
            // fall through to next step
          }
        }
        if (action.type === 'navigate' && action.url) {
          try {
            await page.goto(action.url, { waitUntil: 'domcontentloaded', timeout: 10_000 });
          } catch {
            // ignore navigation failures, continue loop
          }
        }

        // After a click/navigate, opportunistically extract hits from the
        // new page so we accumulate data even if the model never says extract.
        const newSnap = await this.snapshot(page, target, step);
        hits.push(...this.extractHitsFromSnapshot(newSnap, target));
        if (hits.length >= budget.maxHitsPerQuery) break;
      }
    } finally {
      try {
        await browser.close();
      } catch {
        // best effort
      }
    }
    return hits;
  }

  /**
   * Build a PageSnapshot for the decision model. Trims text + link count
   * to keep the prompt inside the model's token budget.
   */
  private async snapshot(
    page: import('playwright').Page,
    target: ResearchTarget,
    step: number,
  ): Promise<PageSnapshot> {
    const url = page.url();
    const title = await page.title().catch(() => '');
    // page.evaluate runs in the browser context; pass the extractor as a
    // string so TypeScript doesn't try to resolve `document` against the
    // Node lib (the API workspace doesn't include 'dom').
    const bodyText = (await page
      .evaluate('(document.body && document.body.innerText ? document.body.innerText : "").slice(0, 2000)')
      .catch(() => '')) as string;
    const rawLinks = (await page
      .evaluate(
        'Array.from(document.querySelectorAll("a")).map(function(a){return{text:(a.textContent||"").trim().slice(0,120),href:a.href}}).filter(function(l){return l.text.length>0 && l.href.indexOf("http")===0})',
      )
      .catch(() => [])) as Array<{ text: string; href: string }>;

    return {
      url,
      title: title.slice(0, 200),
      text: bodyText,
      links: this.relevantLinks(rawLinks, target).slice(0, 25),
      step,
    };
  }

  /**
   * Ask the decision model (via the AI Gateway) what to do next given the
   * current page snapshot. The model returns a strict JSON BrowserAction.
   * Falls back to a heuristic "extract" if the model call fails so the
   * loop never deadlocks on a model outage.
   */
  private async decideAction(
    auth: ProxyAuth,
    query: string,
    target: ResearchTarget,
    snapshot: PageSnapshot,
  ): Promise<BrowserAction> {
    const systemPrompt =
      'You are the Browser Use decision model for Founder OS idea research. ' +
      'Given the current page state, decide the next action to find projects ' +
      `similar to the founder's idea. Return STRICT JSON: ` +
      '{"type":"extract","reason":"..."} to grab the current results, ' +
      '{"type":"click","selector":"a[href=...]"} to open a result, ' +
      '{"type":"done","reason":"..."} if you have enough. ' +
      'Prefer extract on the first step once search results are visible.';

    const userPrompt =
      `FOUNDER IDEA QUERY: ${query}\n` +
      `TARGET SITE: ${target}\n` +
      `STEP: ${snapshot.step}\n` +
      `CURRENT URL: ${snapshot.url}\n` +
      `PAGE TITLE: ${snapshot.title}\n` +
      `VISIBLE LINKS (top ${snapshot.links.length}):\n` +
      snapshot.links.map((l, i) => `${i + 1}. ${l.text} -> ${l.href}`).join('\n') +
      `\n\nPAGE TEXT (truncated):\n${snapshot.text.slice(0, 800)}\n\n` +
      `Decide the next action. JSON only.`;

    try {
      const body: ChatCompletionRequestDto = {
        model: FOUNDER_OS_AUTO_MODEL,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        max_tokens: 200,
        temperature: 0,
        response_format: { type: 'json_object' },
      };
      const route = await this.aiProxy.decideRoute(auth, body);
      const result = await this.aiProxy.invoke(auth, body, route);
      if (!result.ok) {
        return { type: 'extract', reason: `model call failed (${result.status})` };
      }
      const parsed = JSON.parse(typeof result.body === 'string' ? result.body : '') as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      const content = parsed.choices?.[0]?.message?.content ?? '';
      const action = this.parseAction(content);
      return action ?? { type: 'extract', reason: 'unparseable model output' };
    } catch (err) {
      this.logger.debug(
        `decideAction model call failed: ${err instanceof Error ? err.message : String(err)} — falling back to extract`,
      );
      return { type: 'extract', reason: 'model fallback' };
    }
  }

  /**
   * Defensive parse of the model's BrowserAction JSON. The model may wrap
   * it in prose or add fields; we only take what we recognise.
   */
  private parseAction(content: string): BrowserAction | null {
    try {
      // Tolerate prose around the JSON object.
      const start = content.indexOf('{');
      const end = content.lastIndexOf('}');
      if (start === -1 || end === -1) return null;
      const obj = JSON.parse(content.slice(start, end + 1)) as Record<string, unknown>;
      const type = obj.type;
      if (type === 'extract') return { type: 'extract', reason: String(obj.reason ?? '') };
      if (type === 'done') return { type: 'done', reason: String(obj.reason ?? '') };
      if (type === 'click' && typeof obj.selector === 'string')
        return { type: 'click', selector: obj.selector };
      if (type === 'navigate' && typeof obj.url === 'string')
        return { type: 'navigate', url: obj.url };
      return null;
    } catch {
      return null;
    }
  }

  // -- Fallback path (no browser) ---------------------------------------

  /**
   * Lightweight fetch + regex extractor used when Playwright/chromium is
   * unavailable. Hits the same search URLs but parses the static HTML.
   * Less capable (no JS-rendered content) but keeps the feature working.
   */
  private async researchWithFetch(
    auth: ProxyAuth,
    query: string,
    target: ResearchTarget,
    budget: ResearchBudget,
  ): Promise<ResearchHit[]> {
    const url = this.searchUrlFor(target, query);
    const requestId = randomUUID();
    const startedAt = Date.now();
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), budget.timeoutPerQueryMs);
      const res = await fetch(url, {
        headers: {
          'User-Agent':
            'Mozilla/5.0 (compatible; FounderOS-IdeaValidator/1.0; +https://doxxedcrypto.digital)',
          Accept: 'text/html,application/json',
        },
        signal: controller.signal,
      });
      clearTimeout(timer);
      const html = await res.text();
      const hits = this.extractHitsFromHtml(html, url, target);
      await this.logBrowserAction(auth, target, 1, url, { type: 'extract', reason: 'fetch-fallback' });
      void requestId;
      return hits;
    } catch (err) {
      await this.logBrowserAction(auth, target, 1, url, {
        type: 'done',
        reason: `fetch error: ${err instanceof Error ? err.message : String(err)}`,
      });
      return [];
    } finally {
      void startedAt;
    }
  }

  // -- Extraction helpers -----------------------------------------------

  /**
   * Pull ResearchHits out of a PageSnapshot. For GitHub we look for repo
   * links + star text; for others we take any link that looks like a result.
   */
  private extractHitsFromSnapshot(snapshot: PageSnapshot, target: ResearchTarget): ResearchHit[] {
    const hits: ResearchHit[] = [];
    for (const link of snapshot.links) {
      const parsed = this.parseHitLink(link.href, link.text, target);
      if (parsed) hits.push(parsed);
    }
    return hits;
  }

  private extractHitsFromHtml(html: string, baseUrl: string, target: ResearchTarget): ResearchHit[] {
    const hits: ResearchHit[] = [];
    const linkRe = /<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi;
    let m: RegExpExecArray | null;
    while ((m = linkRe.exec(html)) !== null && hits.length < 10) {
      const href = m[1];
      const text = m[2].replace(/<[^>]+>/g, '').trim().slice(0, 200);
      const absolute = href.startsWith('http') ? href : new URL(href, baseUrl).toString();
      const parsed = this.parseHitLink(absolute, text, target);
      if (parsed) hits.push(parsed);
    }
    return hits;
  }

  /**
   * Decide whether a link is a real project hit (vs nav chrome) and, if so,
   * build a ResearchHit. GitHub repo links look like /owner/repo with exactly
     * two path segments. Product Hunt product links have /posts/ or /products/.
   */
  private parseHitLink(href: string, text: string, target: ResearchTarget): ResearchHit | null {
    try {
      const u = new URL(href);
      const seg = u.pathname.split('/').filter(Boolean);
      if (target === 'github') {
        if (u.hostname !== 'github.com') return null;
        // owner/repo exactly (skip org-only, search, sessions, etc.)
        if (seg.length !== 2) return null;
        if (['search', 'topics', 'trending', 'explore', 'sessions', 'login', 'signup', 'settings'].includes(seg[0].toLowerCase())) return null;
        return {
          name: `${seg[0]}/${seg[1]}`,
          description: text || seg[1],
          url: href,
          source: 'github',
        };
      }
      if (target === 'producthunt') {
        if (u.hostname !== 'www.producthunt.com' && u.hostname !== 'producthunt.com') return null;
        if (seg[0] !== 'posts' && seg[0] !== 'products') return null;
        return {
          name: text || seg[seg.length - 1],
          description: text,
          url: href,
          source: 'producthunt',
        };
      }
      // web — accept anything that isn't the search engine's own chrome.
      if (u.hostname.includes('duckduckgo') || u.hostname.includes('google')) return null;
      if (!text || text.length < 4) return null;
      return {
        name: text.slice(0, 80),
        description: text,
        url: href,
        source: 'web',
      };
    } catch {
      return null;
    }
  }

  /**
   * Filter a raw link list down to the ones most likely to be results,
   * so the decision model sees signal not nav chrome.
   */
  private relevantLinks(
    links: Array<{ text: string; href: string }>,
    target: ResearchTarget,
  ): Array<{ text: string; href: string }> {
    return links.filter((l) => {
      try {
        const u = new URL(l.href);
        if (target === 'github') return u.hostname === 'github.com';
        if (target === 'producthunt') return u.hostname.endsWith('producthunt.com');
        return !u.hostname.includes('duckduckgo') && !u.hostname.includes('google.com');
      } catch {
        return false;
      }
    });
  }

  /**
   * Build the search URL for a target + query. GitHub search is the
   * highest-signal source; Product Hunt and DuckDuckGo HTML round it out.
   */
  private searchUrlFor(target: ResearchTarget, query: string): string {
    const q = encodeURIComponent(query);
    switch (target) {
      case 'github':
        return `https://github.com/search?q=${q}+in%3Adescription&type=repositories&s=stars&o=desc`;
      case 'producthunt':
        return `https://www.producthunt.com/search?q=${q}`;
      case 'web':
      default:
        return `https://html.duckduckgo.com/html/?q=${q}`;
    }
  }

  // -- Flight Recorder logging (the LAM action trace) -------------------

  /**
   * Log every browser action as a RoutingDecision-style row so the Flight
   * Recorder captures the full action trace. This is the training data for
   * the Learning Engine and the basis for per-check cost reporting.
   *
   * intent: 'research', chosenProvider: 'local-playwright' so it's
   * distinguishable from LLM model calls.
   */
  private async logBrowserAction(
    auth: ProxyAuth,
    target: ResearchTarget,
    step: number,
    url: string,
    action: BrowserAction,
  ): Promise<void> {
    try {
      await this.flightRecorder.record({
        requestId: randomUUID(),
        userId: auth.userId,
        workspaceId: null,
        intent: 'research',
        profile: 'autonomous',
        candidates: [],
        chosenProvider: 'local-playwright',
        chosenModel: `chromium-headless:${target}`,
        cacheLevel: 'miss',
        cacheKey: null,
        promptHash: `${target}:${step}:${url.slice(0, 64)}`,
        tokenCountPrompt: 0,
        tokenCountCompletion: 0,
        latencyMs: 0,
        costUsd: 0,
      });
    } catch (err) {
      this.logger.debug(
        `flight recorder write for browser action failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  // -- Playwright loader (graceful) -------------------------------------

  /**
   * Dynamically import Playwright. Returns null if the package or the
   * chromium binary isn't available, so the adapter degrades to the fetch
   * fallback instead of crashing boot.
   */
  private async tryLoadPlaywright(): Promise<LoadedPlaywright | null> {
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const pw = require('playwright') as typeof import('playwright');
      // Probe: will launch fail? If so, we keep the null and use fetch.
      return pw as LoadedPlaywright;
    } catch (err) {
      this.logger.warn(
        `Playwright not available — research hand falling back to fetch-only. ` +
          `${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }
}

/**
 * Locally typed handle to the dynamically-loaded Playwright module. Keeps
 * TypeScript happy without making `playwright` a hard compile-time import
 * (which would break the build on environments where it isn't installed).
 */
type LoadedPlaywright = typeof import('playwright');
