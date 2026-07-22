"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEFAULT_MAX_RETRIES = exports.DEFAULT_FIM_TIMEOUT_MS = exports.DEFAULT_CHAT_TIMEOUT_MS = void 0;
exports.redactSecrets = redactSecrets;
exports.gatewayUserMessage = gatewayUserMessage;
exports.parseRetryAfter = parseRetryAfter;
exports.computeBackoffMs = computeBackoffMs;
exports.callGateway = callGateway;
exports.validateFimRequest = validateFimRequest;
exports.validateFimResponse = validateFimResponse;
exports.callFim = callFim;
exports.gatewayJson = gatewayJson;
// ─── Constants ──────────────────────────────────────────────────────────────
exports.DEFAULT_CHAT_TIMEOUT_MS = 60_000;
exports.DEFAULT_FIM_TIMEOUT_MS = 10_000;
exports.DEFAULT_MAX_RETRIES = 3;
const BACKOFF_BASE_MS = 1_000;
const BACKOFF_JITTER = 0.25;
/** FIM endpoint — OpenAI-compatible `/completions` (not `/chat/completions`). */
const FIM_PATH = '/completions';
// ─── Helpers ────────────────────────────────────────────────────────────────
/** Prefer full `FounderNode …` headers; wrap bare `fos_…` as Bearer. */
function authorizationHeader(credential) {
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
function redactSecrets(input) {
    let text;
    try {
        text = typeof input === 'string' ? input : JSON.stringify(input);
    }
    catch {
        text = String(input);
    }
    // Authorization header (value runs to end-of-line or end-of-string):
    //   "Authorization: Bearer xxx"
    //   "Authorization: FounderNode id:tok"
    //   "authorization":"Bearer xxx"
    text = text.replace(/((?:authorization|bearer|x-api-key|x-founder-node)["']?\s*[:=]\s*)(?:["']?)[^\n\r"',}]+/gi, '$1***');
    // nodeToken field on object literals / query strings.
    text = text.replace(/("nodeToken"\s*:\s*)"[^"]*"/gi, '$1"***"');
    text = text.replace(/(nodeToken=)[^&\s"]+/gi, '$1***');
    // api_key field — covers OpenAI-compat payloads.
    text = text.replace(/("api_key"\s*:\s*)"[^"]*"/gi, '$1"***"');
    text = text.replace(/(api_key=)[^&\s"]+/gi, '$1***');
    return text;
}
function responseMessage(body) {
    const trimmed = body.trim();
    if (!trimmed || /<\/?(?:html|body|head|title|!doctype)\b/i.test(trimmed)) {
        return null;
    }
    try {
        const parsed = JSON.parse(trimmed);
        const nested = typeof parsed.error === 'object' && parsed.error !== null
            ? parsed.error.message
            : undefined;
        const candidate = nested ?? parsed.message ?? parsed.error;
        if (typeof candidate === 'string' && candidate.trim()) {
            return redactSecrets(candidate.trim()).slice(0, 240);
        }
    }
    catch {
        if (/^[\w\s.,:;!?()'"/@+-]+$/.test(trimmed)) {
            return redactSecrets(trimmed).slice(0, 240);
        }
    }
    return null;
}
function isProviderCredentialFailure(body) {
    const detail = responseMessage(body);
    return Boolean(detail &&
        /(?:authentication\s+fails?|invalid[^.]{0,40}api\s*key|api\s*key[^.]{0,40}invalid)/i.test(detail));
}
/** Convert an HTTP/gateway failure into safe, actionable chat copy. */
function gatewayUserMessage(status, body = '', retryAfterMs = null) {
    const detail = responseMessage(body);
    if (status === 0) {
        return 'Founder AI could not reach Founder OS. Check your connection, then try again or switch to Local mode.';
    }
    if (isProviderCredentialFailure(body)) {
        return 'The selected AI provider is unavailable. Open Founder Connect to repair it or choose another model.';
    }
    if (status === 401 || status === 403) {
        return `Your Founder connection has expired (auth expired, ${status}). Open Founder Connect and sign in again.`;
    }
    if (status === 429) {
        const wait = retryAfterMs && retryAfterMs > 0
            ? ` Try again in about ${Math.max(1, Math.ceil(retryAfterMs / 1000))} seconds.`
            : ' Try again shortly.';
        return `Founder AI is rate limited and busy.${wait}`;
    }
    if (status >= 500) {
        return `Founder AI is temporarily unavailable (gateway ${status}). Your local files are safe. Try again shortly or switch to Local mode.`;
    }
    if (status >= 400) {
        return detail
            ? `Founder OS rejected this request (${status}): ${detail}`
            : `Founder OS rejected this request (${status}). Review Founder Connect and try again.`;
    }
    return 'Founder AI could not complete this request. Try again or open Founder Connect.';
}
/**
 * Parse a `Retry-After` header value to milliseconds. Returns null if the
 * header is missing or unparsable. Accepts either delta-seconds or an HTTP
 * date (RFC 7231 §7.1.3).
 */
function parseRetryAfter(headerValue) {
    if (!headerValue)
        return null;
    const trimmed = headerValue.trim();
    if (!trimmed)
        return null;
    // Pure-integer = seconds.
    if (/^\d+$/.test(trimmed)) {
        const secs = Number.parseInt(trimmed, 10);
        if (!Number.isFinite(secs) || secs < 0)
            return null;
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
function computeBackoffMs(attempt) {
    const base = BACKOFF_BASE_MS * 2 ** attempt; // 1s, 2s, 4s …
    const jitterMagnitude = base * BACKOFF_JITTER;
    const jitter = (Math.random() * 2 - 1) * jitterMagnitude;
    return Math.max(0, Math.round(base + jitter));
}
function sleep(ms, signal) {
    return new Promise((resolve) => {
        let handle;
        const onAbort = () => {
            if (handle)
                clearTimeout(handle);
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
/**
 * POST `/chat/completions` with `stream: true` and pump SSE chunks into
 * `onToken`. Resolves when the stream ends (`[DONE]` or socket close). Rejects
 * on auth / network / non-2xx errors (after retries). Honors `token` (VS Code
 * CancellationToken) and `options.signal` (AbortSignal) by aborting the
 * in-flight fetch.
 */
async function callGateway(client, options, callbacks, token, extra = {}) {
    const url = `${client.baseUrl}/chat/completions`;
    const maxRetries = options.maxRetries ?? exports.DEFAULT_MAX_RETRIES;
    const fetchImpl = extra.fetchImpl ?? fetch;
    const sleepImpl = extra.sleepImpl ?? sleep;
    const userSignal = extra.signal;
    const timeoutMs = options.timeoutMs ?? exports.DEFAULT_CHAT_TIMEOUT_MS;
    const log = callbacks.log ?? (() => { });
    for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
        if (token.isCancellationRequested || userSignal?.aborted)
            return;
        // Fresh controller per attempt. The user cancellation listener aborts
        // this controller so the in-flight fetch is cancelled immediately. We
        // dispose the per-attempt listener in the finally below.
        const attemptController = new AbortController();
        const attemptCancelDisposable = token.onCancellationRequested(() => attemptController.abort());
        const attemptTimeout = setTimeout(() => attemptController.abort(), timeoutMs);
        try {
            const headers = {
                'Content-Type': 'application/json',
                Authorization: authorizationHeader(client.bearer),
                Accept: 'text/event-stream',
            };
            if (options.executionProfile && options.executionProfile !== 'auto') {
                headers['X-Execution-Profile'] = options.executionProfile;
            }
            const body = {
                model: options.model,
                messages: options.messages,
                stream: true,
            };
            if (typeof options.temperature === 'number')
                body.temperature = options.temperature;
            if (typeof options.maxTokens === 'number')
                body.max_tokens = options.maxTokens;
            if (options.founderOsMetadata)
                body.founder_os_metadata = true;
            if (options.tools?.length)
                body.tools = options.tools;
            if (options.tools?.length && options.toolChoice)
                body.tool_choice = options.toolChoice;
            log('info', `→ POST ${url} (attempt ${attempt + 1}/${maxRetries + 1})`);
            let res;
            try {
                res = await fetchImpl(url, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(body),
                    signal: attemptController.signal,
                });
            }
            catch (err) {
                if (token.isCancellationRequested || userSignal?.aborted)
                    return;
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
                if (isProviderCredentialFailure(text)) {
                    log('warn', 'upstream AI provider credentials were rejected');
                    callbacks.onProviderError?.(res.status, text);
                    callbacks.onError?.(res.status, text);
                    throw new Error(gatewayUserMessage(res.status, text));
                }
                log('warn', redactSecrets(`auth expired (${res.status}): ${text.slice(0, 200)}`));
                callbacks.onAuthExpired?.(res.status);
                callbacks.onError?.(res.status, text);
                throw new Error(gatewayUserMessage(res.status, text));
            }
            // 429 — rate limited. Read Retry-After, surface, no retry.
            if (res.status === 429) {
                const text = await res.text().catch(() => '');
                const retryAfterMs = parseRetryAfter(res.headers.get('retry-after'));
                log('warn', redactSecrets(`rate limited (429); retry-after=${res.headers.get('retry-after') ?? 'absent'} (${retryAfterMs}ms)`));
                callbacks.onRateLimited?.(retryAfterMs);
                callbacks.onError?.(res.status, text);
                throw new Error(gatewayUserMessage(res.status, text, retryAfterMs));
            }
            // 4xx (non-401/403/429) — deterministic, do not retry.
            if (res.status >= 400 && res.status < 500) {
                const text = await res.text().catch(() => '');
                log('warn', redactSecrets(`client error (${res.status}): ${text.slice(0, 200)}`));
                callbacks.onError?.(res.status, text);
                throw new Error(gatewayUserMessage(res.status, text));
            }
            // 5xx — retry up to cap; surface provider error if exhausted.
            if (res.status >= 500) {
                const text = await res.text().catch(() => '');
                if (attempt < maxRetries) {
                    log('warn', redactSecrets(`upstream ${res.status}; will retry: ${text.slice(0, 200)}`));
                    await sleepImpl(computeBackoffMs(attempt), userSignal);
                    continue;
                }
                log('error', redactSecrets(`upstream ${res.status} (no retries left)`));
                callbacks.onProviderError?.(res.status, text);
                callbacks.onError?.(res.status, text);
                throw new Error(gatewayUserMessage(res.status, text));
            }
            // 2xx with body — stream it. (No retry on stream-time errors; partial
            // tokens are lost and the user-visible message will be partial.)
            if (!res.ok || !res.body) {
                const text = await res.text().catch(() => '');
                callbacks.onError?.(res.status, text);
                throw new Error(gatewayUserMessage(res.status, text));
            }
            try {
                await pumpSseStream(res.body, callbacks, attemptController.signal, makeToolCallAggregator());
                return; // success
            }
            catch (err) {
                if (token.isCancellationRequested || userSignal?.aborted)
                    return;
                const message = err instanceof Error ? err.message : String(err);
                if (attempt < maxRetries) {
                    log('warn', redactSecrets(`stream error (${message}); will retry`));
                    await sleepImpl(computeBackoffMs(attempt), userSignal);
                    continue;
                }
                callbacks.onError?.(0, `Stream error: ${message}`);
                throw new Error(`Founder OS gateway stream error: ${message}`);
            }
        }
        finally {
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
async function pumpSseStream(body, callbacks, signal, toolCalls) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    // Raw byte buffer — accumulates partial UTF-8 across reads. We decode with
    // `{stream:true}` so multi-byte chars split across chunks stay intact.
    let buffer = '';
    // Per-event data accumulator — multiple `data:` lines per event are joined
    // with `\n` per the SSE spec.
    const log = callbacks.log ?? (() => { });
    try {
        for (;;) {
            if (signal.aborted)
                return;
            const { done, value } = await reader.read();
            if (done)
                break;
            buffer += decoder.decode(value, { stream: true });
            // SSE events are separated by a blank line. Handle both `\n\n` and
            // `\r\n\r\n` (findEventBoundary normalizes both).
            let sepIndex;
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
    }
    finally {
        try {
            reader.releaseLock();
        }
        catch {
            /* noop */
        }
    }
}
function findEventBoundary(buf) {
    const lf = buf.indexOf('\n\n');
    const crlf = buf.indexOf('\r\n\r\n');
    if (lf === -1)
        return crlf;
    if (crlf === -1)
        return lf;
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
function handleSseEvent(rawEvent, callbacks, log, toolCalls) {
    // Split into lines and normalize CRLF → LF.
    const lines = rawEvent.split(/\r?\n/);
    const dataLines = [];
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
    if (dataLines.length === 0)
        return;
    const payload = dataLines.join('\n').trim();
    if (payload === '[DONE]') {
        // Terminal marker — clean close, no chunk emitted.
        toolCalls.flushThrough(callbacks);
        return;
    }
    if (payload.length === 0)
        return;
    let evt;
    try {
        const parsed = JSON.parse(payload);
        if (!parsed || typeof parsed !== 'object') {
            log('warn', redactSecrets(`malformed SSE payload (not object): ${payload.slice(0, 200)}`));
            return;
        }
        evt = parsed;
    }
    catch {
        // Malformed frame — skip with a warning rather than killing the stream.
        log('warn', redactSecrets(`malformed SSE frame skipped: ${payload.slice(0, 200)}`));
        return;
    }
    // Non-standard Founder OS metadata pre-line (design report §8.2).
    if ('founderOs' in evt) {
        const meta = evt.founderOs;
        callbacks.onMetadata?.(meta);
        return;
    }
    const choices = evt.choices;
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
function isToolCallDelta(v) {
    if (!v || typeof v !== 'object')
        return false;
    const tcd = v;
    return (typeof tcd.index === 'number' &&
        Number.isInteger(tcd.index) &&
        (tcd.function === undefined ||
            (typeof tcd.function === 'object' && tcd.function !== null)));
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
    buffer = new Map();
    currentIndex = null;
    aggregate(delta, callbacks) {
        // If the gateway switched to a new tool-call index, flush the previous.
        if (this.currentIndex !== null && this.currentIndex !== delta.index) {
            const prev = this.buffer.get(this.currentIndex);
            if (prev) {
                callbacks.onToolCall?.(prev);
                this.buffer.delete(this.currentIndex);
            }
        }
        this.currentIndex = delta.index;
        const existing = this.buffer.get(delta.index) ??
            {
                index: delta.index,
                id: '',
                name: '',
                arguments: '',
            };
        if (delta.id)
            existing.id = delta.id;
        if (delta.function?.name)
            existing.name += delta.function.name;
        if (delta.function?.arguments)
            existing.arguments += delta.function.arguments;
        this.buffer.set(delta.index, existing);
    }
    /**
     * Flush all buffered calls through the given callback (final close).
     * Called when the stream ends or a `[DONE]` marker arrives.
     */
    flushThrough(callbacks) {
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
function makeToolCallAggregator() {
    return new ToolCallAggregator();
}
/**
 * Validate a FIM request — `prefix` and `suffix` must be strings (empty ok).
 * Throws on invalid input. Public so tests can pin the rule.
 */
function validateFimRequest(req) {
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
function validateFimResponse(body) {
    if (!body || typeof body !== 'object') {
        throw new Error('FIM response must be an object');
    }
    const choices = body.choices;
    if (!Array.isArray(choices) || choices.length === 0) {
        throw new Error('FIM response.choices must be a non-empty array');
    }
    const first = choices[0];
    if (!first || typeof first !== 'object') {
        throw new Error('FIM response.choices[0] must be an object');
    }
    if (typeof first.text !== 'string') {
        throw new Error('FIM response.choices[0].text must be a string');
    }
}
/**
 * POST `/completions` (non-streaming FIM). The gateway converts
 * `{prefix, suffix}` into a provider-specific FIM payload (e.g. DeepSeek's
 * `<｜fim_begin｜>…<｜fim_hole｜>…<｜fim_end｜>` template) — this client only
 * sends the OpenAI-compat shape and validates the response.
 */
async function callFim(client, req, token, extra = {}) {
    validateFimRequest(req);
    const url = `${client.baseUrl}${FIM_PATH}`;
    const timeoutMs = req.timeoutMs ?? exports.DEFAULT_FIM_TIMEOUT_MS;
    const fetchImpl = extra.fetchImpl ?? fetch;
    const userSignal = extra.signal;
    const controller = new AbortController();
    const cancellationListener = token.onCancellationRequested(() => controller.abort());
    userSignal?.addEventListener('abort', () => controller.abort(), { once: true });
    const timeoutHandle = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const body = {
            model: req.model,
            // OpenAI-compat FIM template — gateway knows how to translate.
            prompt: req.prefix,
            suffix: req.suffix,
            stream: false,
            max_tokens: 64,
        };
        if (req.language)
            body.language = req.language;
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
            throw new Error(gatewayUserMessage(res.status, text));
        }
        const json = await res.json();
        validateFimResponse(json);
        return { text: json.choices[0].text, raw: json };
    }
    finally {
        clearTimeout(timeoutHandle);
        cancellationListener.dispose();
    }
}
// ─── Generic JSON helper (kept for /models etc.) ────────────────────────────
/**
 * Non-streaming helper for low-level calls (e.g. GET /models). Not used by the
 * chat provider today but kept for the management command / future UI.
 */
async function gatewayJson(client, method, path, body, token) {
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
            throw new Error(gatewayUserMessage(res.status, text));
        }
        return (await res.json());
    }
    finally {
        cancellationListener?.dispose();
    }
}
//# sourceMappingURL=gateway-client.js.map