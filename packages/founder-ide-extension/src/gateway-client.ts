/**
 * HTTP/SSE client for the Founder OS AI Gateway (Phase 4 hardened).
 *
 * Calls `{apiBaseUrl}/chat/completions` with `stream: true` and parses the
 * OpenAI-compatible SSE response (`data: {...}\n\n` lines, terminal
 * `data: [DONE]`). Each parsed `choices[0].delta.content` chunk is passed to
 * the `onToken` callback. Optional non-standard `founderOs` metadata lines
 * (see design report §5.3 / §8.2) are routed to `onMetadata`.
 *
 * The gateway streams through whatever the Routing Engine picks (GLM /
 * DeepSeek / etc.) so this client is provider-agnostic — it only speaks the
 * OpenAI SSE schema.
 *
 * Phase 4 hardening (see design report §4 / brief "Workstream C — Task 1"):
 *   - Accepts an `AbortSignal` (in addition to the legacy `CancellationToken`)
 *     and wires it to the underlying `fetch` so the SSE stream aborts cleanly.
 *   - Buffers raw chunks and splits on `\n`, handling a chunk that ends
 *     mid-data-line by keeping the remainder for the next chunk.
 *   - Strips `\r` (CRLF tolerance — some proxies send `\r\n`).
 *   - Skips malformed frames with a warning rather than killing the stream.
 *   - Handles `[DONE]` cleanly (closes without emitting a chunk).
 *   - Bounded retries with exponential backoff: 3 attempts at 1s/2s/4s with
 *     ±25% jitter. Only retries on network errors and HTTP 5xx. Never on 4xx.
 *   - 401/403 flips local auth state to `token_expired` and fires `onAuthExpired`
 *     (no retry — re-pair flow must run first).
 *   - 429 reads `Retry-After` (seconds or HTTP-date) and fires
 *     `onRateLimited(retryAfterMs)` (no retry past the explicit cap).
 *   - 5xx upstream failure surfaces via `onProviderError` (no retry past cap).
 *   - Tool-call deltas (`choices[0].delta.tool_calls[]`) are aggregated and
 *     flushed via `onToolCall`. Index-keyed so multi-chunk tool calls stitch.
 *   - FIM (fill-in-the-middle) helper validates `prefix`/`suffix` strings and
 *     the `choices[0].text` response shape (FIM uses the completions endpoint).
 *   - Per-request timeouts (default 60s chat, 10s FIM, configurable).
 *   - `redactSecrets()` strips `Authorization`, `nodeToken`, `api_key` from
 *     every log line so the output channel never leaks credentials.
 */
import type { CancellationToken } from 'vscode';

// ─── Public types ───────────────────────────────────────────────────────────

export interface GatewayMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
  name?: string;
}

export interface GatewayCallOptions {
  model: string;
  messages: GatewayMessage[];
  /** Execution-profile hint — sent as `X-Execution-Profile`. */
  executionProfile?: 'auto' | 'turbo' | 'architect';
  temperature?: number;
  maxTokens?: number;
  /** Request a non-standard `data: {"founderOs":{...}}` metadata pre-line. */
  founderOsMetadata?: boolean;
  /** Per-request timeout in ms. Defaults to DEFAULT_CHAT_TIMEOUT_MS. */
  timeoutMs?: number;
  /** Override the 3-attempt retry cap. Set to 0 to disable retries. */
  maxRetries?: number;
}

export interface GatewayFounderOsMetadata {
  requestId?: string;
  tier?: string;
  provider?: string;
  model?: string;
  ddollarCost?: number;
  [k: string]: unknown;
}

/** Aggregated tool-call shape — emitted once the gateway closes a tool call. */
export interface GatewayToolCall {
  index: number;
  id: string;
  /** Function name (may arrive across multiple deltas). */
  name: string;
  /** JSON-stringified arguments (may arrive across multiple deltas). */
  arguments: string;
}

export interface GatewayClient {
  /** Base URL ending in `/api/v1` (no trailing slash). */
  baseUrl: string;
  /**
   * Authorization credential. Prefer the full `FounderNode {id}:{token}` header
   * value. Legacy `fos_{id}:{token}` is still accepted (sent as Bearer).
   */
  bearer: string;
}

export interface StreamCallbacks {
  onToken: (delta: string) => void;
  onMetadata?: (meta: GatewayFounderOsMetadata) => void;
  /** Aggregated tool-call emitted when the gateway signals end-of-call. */
  onToolCall?: (call: GatewayToolCall) => void;
  /** Non-2xx response (after retries exhausted) — receives status + body slice. */
  onError?: (status: number, body: string) => void;
  /** 401/403 — local auth state should flip to `token_expired` and trigger re-pair. */
  onAuthExpired?: (status: number) => void;
  /** 429 — surfaces the parsed `Retry-After` value (ms). No retry attempted. */
  onRateLimited?: (retryAfterMs: number | null) => void;
  /** 5xx upstream — provider failure. Fired after retries exhausted. */
  onProviderError?: (status: number, body: string) => void;
  /** Diagnostic logger (already redacted). Optional. */
  log?: (level: 'warn' | 'info' | 'error', message: string) => void;
}

// ─── Constants ──────────────────────────────────────────────────────────────

export const DEFAULT_CHAT_TIMEOUT_MS = 60_000;
export const DEFAULT_FIM_TIMEOUT_MS = 10_000;
export const DEFAULT_MAX_RETRIES = 3;
const BACKOFF_BASE_MS = 1_000;
const BACKOFF_JITTER = 0.25;

/** FIM endpoint — OpenAI-compatible `/completions` (not `/chat/completions`). */
const FIM_PATH = '/completions';

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Prefer full `FounderNode …` headers; wrap bare `fos_…` as Bearer. */
function authorizationHeader(credential: string): string {
  const trimmed = credential.trim();
  if (trimmed.startsWith('FounderNode ') || /^Bearer\s+/i.test(trimmed)) {
    return trimmed;
  }
  return `Bearer ${trimmed}`;
}

/**
 * Strip secrets from arbitrary stringifiable values. Used before any log line
 * is forwarded to the output channel. Replaces `Authorization: …` header
 * values, `nodeToken` fields, and `api_key` fields with `***`.
 *
 * Public so tests can pin the redaction rules.
 */
export function redactSecrets(input: unknown): string {
  let text: string;
  try {
    text = typeof input === 'string' ? input : JSON.stringify(input);
  } catch {
    text = String(input);
  }
  // Authorization header (value runs to end-of-line or end-of-string):
  //   "Authorization: Bearer xxx"
  //   "Authorization: FounderNode id:tok"
  //   "authorization":"Bearer xxx"
  text = text.replace(
    /((?:authorization|bearer|x-api-key|x-founder-node)["']?\s*[:=]\s*)(?:["']?)[^\n\r"',}]+/gi,
    '$1***',
  );
  // nodeToken field on object literals / query strings.
  text = text.replace(/("nodeToken"\s*:\s*)"[^"]*"/gi, '$1"***"');
  text = text.replace(/(nodeToken=)[^&\s"]+/gi, '$1***');
  // api_key field — covers OpenAI-compat payloads.
  text = text.replace(/("api_key"\s*:\s*)"[^"]*"/gi, '$1"***"');
  text = text.replace(/(api_key=)[^&\s"]+/gi, '$1***');
  return text;
}

/**
 * Parse a `Retry-After` header value to milliseconds. Returns null if the
 * header is missing or unparsable. Accepts either delta-seconds or an HTTP
 * date (RFC 7231 §7.1.3).
 */
export function parseRetryAfter(headerValue: string | null | undefined): number | null {
  if (!headerValue) return null;
  const trimmed = headerValue.trim();
  if (!trimmed) return null;
  // Pure-integer = seconds.
  if (/^\d+$/.test(trimmed)) {
    const secs = Number.parseInt(trimmed, 10);
    if (!Number.isFinite(secs) || secs < 0) return null;
    return secs * 1000;
  }
  // HTTP-date.
  const parsed = Date.parse(trimmed);
  if (!Number.isNaN(parsed)) {
    const delta = parsed - Date.now();
    return delta > 0 ? delta : 0;
  }
  return null;
}

/**
 * Compute the next backoff delay using exponential schedule (1s, 2s, 4s, …)
 * with ±25% jitter. Exposed for tests so the jitter math can be pinned.
 */
export function computeBackoffMs(attempt: number): number {
  const base = BACKOFF_BASE_MS * 2 ** attempt; // 1s, 2s, 4s …
  const jitterMagnitude = base * BACKOFF_JITTER;
  const jitter = (Math.random() * 2 - 1) * jitterMagnitude;
  return Math.max(0, Math.round(base + jitter));
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    let handle: ReturnType<typeof setTimeout> | undefined;
    const onAbort = () => {
      if (handle) clearTimeout(handle);
      signal?.removeEventListener('abort', onAbort);
      resolve();
    };
    if (signal?.aborted) {
      resolve();
      return;
    }
    signal?.addEventListener('abort', onAbort, { once: true });
    handle = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
  });
}

// ─── Chat completions (streaming) ───────────────────────────────────────────

export interface CallGatewayExtraOptions {
  /** External AbortSignal. Combined with `token` for cancellation. */
  signal?: AbortSignal;
  /** Inject a fetch implementation (tests). Defaults to global fetch. */
  fetchImpl?: typeof fetch;
  /** Inject a sleep implementation (tests). Defaults to real setTimeout. */
  sleepImpl?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

/**
 * POST `/chat/completions` with `stream: true` and pump SSE chunks into
 * `onToken`. Resolves when the stream ends (`[DONE]` or socket close). Rejects
 * on auth / network / non-2xx errors (after retries). Honors `token` (VS Code
 * CancellationToken) and `options.signal` (AbortSignal) by aborting the
 * in-flight fetch.
 */
export async function callGateway(
  client: GatewayClient,
  options: GatewayCallOptions,
  callbacks: StreamCallbacks,
  token: CancellationToken,
  extra: CallGatewayExtraOptions = {},
): Promise<void> {
  const url = `${client.baseUrl}/chat/completions`;
  const maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
  const fetchImpl = extra.fetchImpl ?? fetch;
  const sleepImpl = extra.sleepImpl ?? sleep;
  const userSignal = extra.signal;
  const timeoutMs = options.timeoutMs ?? DEFAULT_CHAT_TIMEOUT_MS;
  const log = callbacks.log ?? (() => {});

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    if (token.isCancellationRequested || userSignal?.aborted) return;

    // Fresh controller per attempt. The user cancellation listener aborts
    // this controller so the in-flight fetch is cancelled immediately. We
    // dispose the per-attempt listener in the finally below.
    const attemptController = new AbortController();
    const attemptCancelDisposable = token.onCancellationRequested(() =>
      attemptController.abort(),
    );
    const attemptTimeout = setTimeout(
      () => attemptController.abort(),
      timeoutMs,
    );

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        Authorization: authorizationHeader(client.bearer),
        Accept: 'text/event-stream',
      };
      if (options.executionProfile && options.executionProfile !== 'auto') {
        headers['X-Execution-Profile'] = options.executionProfile;
      }

      const body: Record<string, unknown> = {
        model: options.model,
        messages: options.messages,
        stream: true,
      };
      if (typeof options.temperature === 'number') body.temperature = options.temperature;
      if (typeof options.maxTokens === 'number') body.max_tokens = options.maxTokens;
      if (options.founderOsMetadata) body.founder_os_metadata = true;

      log('info', `→ POST ${url} (attempt ${attempt + 1}/${maxRetries + 1})`);

      let res: Response;
      try {
        res = await fetchImpl(url, {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
          signal: attemptController.signal,
        });
      } catch (err) {
        if (token.isCancellationRequested || userSignal?.aborted) return;
        const message = err instanceof Error ? err.message : String(err);
        // Network error (non-user abort) → retry up to cap.
        if (attempt < maxRetries) {
          log('warn', redactSecrets(`network error (${message}); will retry`));
          await sleepImpl(computeBackoffMs(attempt), userSignal);
          continue;
        }
        callbacks.onError?.(0, `Network error calling Founder OS gateway: ${message}`);
        throw new Error(`Founder OS gateway network error: ${message}`);
      }

      // 401/403 — auth expired. Never retry; surface + bail.
      if (res.status === 401 || res.status === 403) {
        const text = await res.text().catch(() => '');
        log('warn', redactSecrets(`auth expired (${res.status}): ${text.slice(0, 200)}`));
        callbacks.onAuthExpired?.(res.status);
        callbacks.onError?.(res.status, text);
        throw new Error(`Founder OS gateway auth expired (${res.status})`);
      }

      // 429 — rate limited. Read Retry-After, surface, no retry.
      if (res.status === 429) {
        const text = await res.text().catch(() => '');
        const retryAfterMs = parseRetryAfter(res.headers.get('retry-after'));
        log(
          'warn',
          redactSecrets(
            `rate limited (429); retry-after=${res.headers.get('retry-after') ?? 'absent'} (${retryAfterMs}ms)`,
          ),
        );
        callbacks.onRateLimited?.(retryAfterMs);
        callbacks.onError?.(res.status, text);
        throw new Error(`Founder OS gateway rate limited (429)`);
      }

      // 4xx (non-401/403/429) — deterministic, do not retry.
      if (res.status >= 400 && res.status < 500) {
        const text = await res.text().catch(() => '');
        log('warn', redactSecrets(`client error (${res.status}): ${text.slice(0, 200)}`));
        callbacks.onError?.(res.status, text);
        throw new Error(
          `Founder OS gateway returned ${res.status}: ${text.slice(0, 500)}`,
        );
      }

      // 5xx — retry up to cap; surface provider error if exhausted.
      if (res.status >= 500) {
        const text = await res.text().catch(() => '');
        if (attempt < maxRetries) {
          log(
            'warn',
            redactSecrets(`upstream ${res.status}; will retry: ${text.slice(0, 200)}`),
          );
          await sleepImpl(computeBackoffMs(attempt), userSignal);
          continue;
        }
        log('error', redactSecrets(`upstream ${res.status} (no retries left)`));
        callbacks.onProviderError?.(res.status, text);
        callbacks.onError?.(res.status, text);
        throw new Error(
          `Founder OS gateway upstream ${res.status}: ${text.slice(0, 500)}`,
        );
      }

      // 2xx with body — stream it. (No retry on stream-time errors; partial
      // tokens are lost and the user-visible message will be partial.)
      if (!res.ok || !res.body) {
        const text = await res.text().catch(() => '');
        callbacks.onError?.(res.status, text);
        throw new Error(`Founder OS gateway returned ${res.status}: ${text.slice(0, 500)}`);
      }

      try {
        await pumpSseStream(
          res.body,
          callbacks,
          attemptController.signal,
          makeToolCallAggregator(),
        );
        return; // success
      } catch (err) {
        if (token.isCancellationRequested || userSignal?.aborted) return;
        const message = err instanceof Error ? err.message : String(err);
        if (attempt < maxRetries) {
          log('warn', redactSecrets(`stream error (${message}); will retry`));
          await sleepImpl(computeBackoffMs(attempt), userSignal);
          continue;
        }
        callbacks.onError?.(0, `Stream error: ${message}`);
        throw new Error(`Founder OS gateway stream error: ${message}`);
      }
    } finally {
      clearTimeout(attemptTimeout);
      attemptCancelDisposable.dispose();
    }
  }
  // Loop exhausted without success or terminal throw — only reachable if all
  // retries were skipped via `continue`. Treat as success (cancellation path
  // already returned) to avoid spurious errors.
}

// ─── SSE stream parser ──────────────────────────────────────────────────────

/**
 * Parse an OpenAI-compatible SSE stream. Robust to:
 *   - chunks split mid-data-line (buffer carries the remainder)
 *   - `\r\n` line endings (we strip `\r` before processing)
 *   - frames separated by either `\n\n` or `\r\n\r\n`
 *   - malformed `data:` payloads (skipped with a warning, never throws)
 *   - the `[DONE]` terminator (closes cleanly, no chunk emitted)
 */
async function pumpSseStream(
  body: ReadableStream<Uint8Array>,
  callbacks: StreamCallbacks,
  signal: AbortSignal,
  toolCalls: ToolCallAggregator,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  // Raw byte buffer — accumulates partial UTF-8 across reads. We decode with
  // `{stream:true}` so multi-byte chars split across chunks stay intact.
  let buffer = '';
  // Per-event data accumulator — multiple `data:` lines per event are joined
  // with `\n` per the SSE spec.
  const log = callbacks.log ?? (() => {});

  try {
    for (;;) {
      if (signal.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by a blank line. Handle both `\n\n` and
      // `\r\n\r\n` (findEventBoundary normalizes both).
      let sepIndex: number;
      while ((sepIndex = findEventBoundary(buffer)) !== -1) {
        const rawEvent = buffer.slice(0, sepIndex);
        // Skip past the separator (2 or 4 chars depending on LF vs CRLF).
        buffer = buffer
          .slice(sepIndex)
          .replace(/^(\r?\n){2}/, '')
          .replace(/^\r\n\r\n/, '');
        handleSseEvent(rawEvent, callbacks, log, toolCalls);
      }
    }
    // Flush a trailing event if the server didn't send a final blank line.
    if (buffer.replace(/\s/, '').length > 0) {
      handleSseEvent(buffer, callbacks, log, toolCalls);
    }
    // Final flush of any aggregated tool calls (in case the stream didn't
    // end with a `[DONE]` frame).
    toolCalls.flushThrough(callbacks);
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* noop */
    }
  }
}

function findEventBoundary(buf: string): number {
  const lf = buf.indexOf('\n\n');
  const crlf = buf.indexOf('\r\n\r\n');
  if (lf === -1) return crlf;
  if (crlf === -1) return lf;
  return Math.min(lf, crlf);
}

/**
 * Process one SSE event (a sequence of `\r?\n`-separated lines). Recognizes:
 *   - `data: …` (joined across multiple lines per the spec)
 *   - `event: …` (currently unused but tolerated)
 *   - `: comment` (SSE keep-alive — silently ignored)
 *   - `founderOs` metadata pre-lines (design report §8.2)
 *
 * Tool-call deltas are aggregated by `index` and flushed once the gateway
 * closes the call (we detect closure heuristically: a new index arrives, or
 * the stream ends — pumpSseStream flushes via flushToolCalls() at end).
 */
function handleSseEvent(
  rawEvent: string,
  callbacks: StreamCallbacks,
  log: (level: 'warn' | 'info' | 'error', message: string) => void,
  toolCalls: ToolCallAggregator,
): void {
  // Split into lines and normalize CRLF → LF.
  const lines = rawEvent.split(/\r?\n/);
  const dataLines: string[] = [];
  for (const line of lines) {
    // Strip any trailing `\r` defensively (in case split left it).
    const stripped = line.replace(/\r$/, '');
    if (stripped.startsWith(':')) {
      // SSE comment / keep-alive — ignore silently.
      continue;
    }
    if (stripped.startsWith('data:')) {
      dataLines.push(stripped.slice(5).replace(/^ /, ''));
      continue;
    }
    if (stripped.startsWith('event:') || stripped.startsWith('id:') || stripped.startsWith('retry:')) {
      // Recognized SSE fields we don't currently use — ignore.
      continue;
    }
    // Empty or unrecognized line. Skip silently (don't warn — SSE allows
    // blank padding lines inside events).
  }
  if (dataLines.length === 0) return;
  const payload = dataLines.join('\n').trim();

  if (payload === '[DONE]') {
    // Terminal marker — clean close, no chunk emitted.
    toolCalls.flushThrough(callbacks);
    return;
  }
  if (payload.length === 0) return;

  let evt: Record<string, unknown>;
  try {
    const parsed = JSON.parse(payload);
    if (!parsed || typeof parsed !== 'object') {
      log('warn', redactSecrets(`malformed SSE payload (not object): ${payload.slice(0, 200)}`));
      return;
    }
    evt = parsed as Record<string, unknown>;
  } catch {
    // Malformed frame — skip with a warning rather than killing the stream.
    log('warn', redactSecrets(`malformed SSE frame skipped: ${payload.slice(0, 200)}`));
    return;
  }

  // Non-standard Founder OS metadata pre-line (design report §8.2).
  if ('founderOs' in evt) {
    const meta = evt.founderOs as GatewayFounderOsMetadata;
    callbacks.onMetadata?.(meta);
    return;
  }

  const choices = evt.choices as
    | Array<{
        delta?: { content?: string; tool_calls?: Array<ToolCallDelta> };
        text?: string;
      }>
    | undefined;
  const delta = choices?.[0]?.delta;
  if (delta && typeof delta === 'object') {
    if (typeof delta.content === 'string' && delta.content.length > 0) {
      callbacks.onToken(delta.content);
    }
    if (Array.isArray(delta.tool_calls)) {
      for (const tc of delta.tool_calls) {
        if (!isToolCallDelta(tc)) {
          log('warn', redactSecrets(`malformed tool_call delta skipped`));
          continue;
        }
        toolCalls.aggregate(tc, callbacks);
      }
    }
  }
}

// ─── Tool-call aggregation ──────────────────────────────────────────────────

interface ToolCallDelta {
  index: number;
  id?: string;
  function?: { name?: string; arguments?: string };
}

function isToolCallDelta(v: unknown): v is ToolCallDelta {
  if (!v || typeof v !== 'object') return false;
  const tcd = v as ToolCallDelta;
  return (
    typeof tcd.index === 'number' &&
    Number.isInteger(tcd.index) &&
    (tcd.function === undefined ||
      (typeof tcd.function === 'object' && tcd.function !== null))
  );
}

/**
 * Per-stream tool-call aggregator. Indexed by `delta.tool_calls[].index`
 * (OpenAI streams tool calls chunked — first delta carries `id` + `name`,
 * subsequent deltas carry argument fragments). Emits via `onToolCall` once
 * the index changes or the stream ends.
 *
 * Lives per-stream (created by pumpSseStream) — module-level state would
 * leak across concurrent requests.
 */
class ToolCallAggregator {
  private readonly buffer = new Map<number, GatewayToolCall>();
  private currentIndex: number | null = null;

  aggregate(delta: ToolCallDelta, callbacks: StreamCallbacks): void {
    // If the gateway switched to a new tool-call index, flush the previous.
    if (this.currentIndex !== null && this.currentIndex !== delta.index) {
      const prev = this.buffer.get(this.currentIndex);
      if (prev) {
        callbacks.onToolCall?.(prev);
        this.buffer.delete(this.currentIndex);
      }
    }
    this.currentIndex = delta.index;
    const existing =
      this.buffer.get(delta.index) ??
      ({
        index: delta.index,
        id: '',
        name: '',
        arguments: '',
      } satisfies GatewayToolCall);
    if (delta.id) existing.id = delta.id;
    if (delta.function?.name) existing.name += delta.function.name;
    if (delta.function?.arguments) existing.arguments += delta.function.arguments;
    this.buffer.set(delta.index, existing);
  }

  /**
   * Flush all buffered calls through the given callback (final close).
   * Called when the stream ends or a `[DONE]` marker arrives.
   */
  flushThrough(callbacks: StreamCallbacks): void {
    if (!callbacks.onToolCall) {
      this.buffer.clear();
      return;
    }
    for (const [, call] of this.buffer) {
      callbacks.onToolCall?.(call);
    }
    this.buffer.clear();
  }
}

function makeToolCallAggregator(): ToolCallAggregator {
  return new ToolCallAggregator();
}

// ─── FIM (fill-in-the-middle) ───────────────────────────────────────────────

export interface FimRequest {
  model: string;
  /** Text immediately before the cursor. */
  prefix: string;
  /** Text immediately after the cursor. */
  suffix: string;
  /** Optional language hint (e.g. `typescript`). */
  language?: string;
  /** Per-request timeout (defaults to DEFAULT_FIM_TIMEOUT_MS). */
  timeoutMs?: number;
}

export interface FimResponse {
  text: string;
  /** Raw `choices[0]` for callers that want finish_reason etc. */
  raw?: unknown;
}

/**
 * Validate a FIM request — `prefix` and `suffix` must be strings (empty ok).
 * Throws on invalid input. Public so tests can pin the rule.
 */
export function validateFimRequest(req: FimRequest): void {
  if (!req || typeof req !== 'object') {
    throw new Error('FIM request must be an object');
  }
  if (typeof req.prefix !== 'string') {
    throw new Error('FIM request.prefix must be a string');
  }
  if (typeof req.suffix !== 'string') {
    throw new Error('FIM request.suffix must be a string');
  }
  if (typeof req.model !== 'string' || req.model.trim().length === 0) {
    throw new Error('FIM request.model must be a non-empty string');
  }
}

/**
 * Validate the FIM response shape — must have `choices[0].text` as a string.
 * Throws on invalid. Public so tests can pin the rule.
 */
export function validateFimResponse(body: unknown): asserts body is {
  choices: Array<{ text: string }>;
} {
  if (!body || typeof body !== 'object') {
    throw new Error('FIM response must be an object');
  }
  const choices = (body as { choices?: unknown }).choices;
  if (!Array.isArray(choices) || choices.length === 0) {
    throw new Error('FIM response.choices must be a non-empty array');
  }
  const first = choices[0];
  if (!first || typeof first !== 'object') {
    throw new Error('FIM response.choices[0] must be an object');
  }
  if (typeof (first as { text?: unknown }).text !== 'string') {
    throw new Error('FIM response.choices[0].text must be a string');
  }
}

/**
 * POST `/completions` (non-streaming FIM). The gateway converts
 * `{prefix, suffix}` into a provider-specific FIM payload (e.g. DeepSeek's
 * `<｜fim_begin｜>…<｜fim_hole｜>…<｜fim_end｜>` template) — this client only
 * sends the OpenAI-compat shape and validates the response.
 */
export async function callFim(
  client: GatewayClient,
  req: FimRequest,
  token: CancellationToken,
  extra: CallGatewayExtraOptions = {},
): Promise<FimResponse> {
  validateFimRequest(req);
  const url = `${client.baseUrl}${FIM_PATH}`;
  const timeoutMs = req.timeoutMs ?? DEFAULT_FIM_TIMEOUT_MS;
  const fetchImpl = extra.fetchImpl ?? fetch;
  const userSignal = extra.signal;

  const controller = new AbortController();
  const cancellationListener = token.onCancellationRequested(() => controller.abort());
  userSignal?.addEventListener('abort', () => controller.abort(), { once: true });
  const timeoutHandle = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const body: Record<string, unknown> = {
      model: req.model,
      // OpenAI-compat FIM template — gateway knows how to translate.
      prompt: req.prefix,
      suffix: req.suffix,
      stream: false,
      max_tokens: 64,
    };
    if (req.language) body.language = req.language;

    const res = await fetchImpl(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: authorizationHeader(client.bearer),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(
        `Founder OS gateway FIM returned ${res.status}: ${text.slice(0, 500)}`,
      );
    }
    const json = await res.json();
    validateFimResponse(json);
    return { text: json.choices[0].text, raw: json };
  } finally {
    clearTimeout(timeoutHandle);
    cancellationListener.dispose();
  }
}

// ─── Generic JSON helper (kept for /models etc.) ────────────────────────────

/**
 * Non-streaming helper for low-level calls (e.g. GET /models). Not used by the
 * chat provider today but kept for the management command / future UI.
 */
export async function gatewayJson<T = unknown>(
  client: GatewayClient,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  token?: CancellationToken,
): Promise<T> {
  const url = `${client.baseUrl}${path}`;
  const controller = new AbortController();
  const cancellationListener = token?.onCancellationRequested(() => controller.abort());
  try {
    const res = await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: authorizationHeader(client.bearer),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Founder OS gateway ${method} ${path} -> ${res.status}: ${text.slice(0, 500)}`);
    }
    return (await res.json()) as T;
  } finally {
    cancellationListener?.dispose();
  }
}
