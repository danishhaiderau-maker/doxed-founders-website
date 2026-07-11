"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.callGateway = callGateway;
exports.gatewayJson = gatewayJson;
/**
 * POST /chat/completions with `stream: true` and pump SSE chunks into `onToken`.
 * Resolves when the stream ends (`[DONE]` or socket close). Rejects on auth /
 * network / non-2xx errors. Honors `token.isCancellationRequested` by aborting
 * the in-flight fetch.
 */
async function callGateway(client, options, callbacks, token) {
    const url = `${client.baseUrl}/chat/completions`;
    const controller = new AbortController();
    const timeoutMs = options.timeoutMs ?? 120_000;
    const cancellationListener = token.onCancellationRequested(() => controller.abort());
    const timeoutHandle = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const headers = {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${client.bearer}`,
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
        let res;
        try {
            res = await fetch(url, {
                method: 'POST',
                headers,
                body: JSON.stringify(body),
                signal: controller.signal,
            });
        }
        catch (err) {
            if (token.isCancellationRequested)
                return;
            const message = err instanceof Error ? err.message : String(err);
            callbacks.onError?.(0, `Network error calling Founder OS gateway: ${message}`);
            throw new Error(`Founder OS gateway network error: ${message}`);
        }
        if (!res.ok || !res.body) {
            const text = await res.text().catch(() => '');
            callbacks.onError?.(res.status, text);
            throw new Error(`Founder OS gateway returned ${res.status}: ${text.slice(0, 500)}`);
        }
        await pumpSseStream(res.body, callbacks, controller.signal);
    }
    finally {
        clearTimeout(timeoutHandle);
        cancellationListener.dispose();
    }
}
/** Parse an OpenAI-compatible SSE stream and emit tokens / metadata. */
async function pumpSseStream(body, callbacks, signal) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
        for (;;) {
            if (signal.aborted)
                return;
            const { done, value } = await reader.read();
            if (done)
                break;
            buffer += decoder.decode(value, { stream: true });
            // SSE events are separated by a blank line. Handle \n\n and \r\n\r\n.
            let sepIndex;
            while ((sepIndex = findEventBoundary(buffer)) !== -1) {
                const rawEvent = buffer.slice(0, sepIndex);
                buffer = buffer.slice(sepIndex).replace(/^(\r?\n){2}/, '');
                handleSseEvent(rawEvent, callbacks);
            }
        }
        // Flush any trailing event.
        if (buffer.trim().length > 0)
            handleSseEvent(buffer, callbacks);
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
function handleSseEvent(rawEvent, callbacks) {
    // An event is a sequence of lines; we only care about `data:` lines.
    // Multi-line `data:` fields are joined with `\n` per the SSE spec.
    const dataLines = [];
    for (const line of rawEvent.split(/\r?\n/)) {
        if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).replace(/^ /, ''));
        }
    }
    if (dataLines.length === 0)
        return;
    const payload = dataLines.join('\n').trim();
    if (payload === '[DONE]')
        return;
    if (payload.length === 0)
        return;
    try {
        const evt = JSON.parse(payload);
        // Non-standard Founder OS metadata pre-line (design report §8.2).
        if (evt && typeof evt === 'object' && 'founderOs' in evt) {
            const meta = evt.founderOs;
            callbacks.onMetadata?.(meta);
            return;
        }
        const choices = evt.choices;
        const delta = choices?.[0]?.delta?.content;
        if (typeof delta === 'string' && delta.length > 0) {
            callbacks.onToken(delta);
        }
    }
    catch {
        /* ignore malformed chunk — upstream occasionally sends keepalives */
    }
}
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
                Authorization: `Bearer ${client.bearer}`,
            },
            body: body ? JSON.stringify(body) : undefined,
            signal: controller.signal,
        });
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            throw new Error(`Founder OS gateway ${method} ${path} -> ${res.status}: ${text.slice(0, 500)}`);
        }
        return (await res.json());
    }
    finally {
        cancellationListener?.dispose();
    }
}
//# sourceMappingURL=gateway-client.js.map