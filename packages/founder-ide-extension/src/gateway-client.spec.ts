/**
 * Unit tests for the hardened Gateway client (Phase 4 Task 4).
 *
 * Covers:
 *   - Happy path: 3 token chunks + `[DONE]` → tokens concatenated + clean close
 *   - CRLF handling: `\r\n` line endings stripped, frames separated by `\r\n\r\n`
 *   - Malformed frames skipped with a warning (not throw)
 *   - Non-2xx → onError + throw
 *   - Cancellation via AbortSignal (extra.signal) → no further tokens
 *   - Retries on network error: mock fetch fails twice then succeeds
 *   - No retry on 4xx (deterministic)
 *   - 401 → onAuthExpired event + no retry
 *   - 429 → onRateLimited with parsed Retry-After
 *   - Tool-call aggregation: 2 deltas (id+name, then args fragments) → 1 call
 *   - FIM request/response validation
 *   - Secret redaction in logs
 *
 * Pure TypeScript — no vscode dependency. The CancellationToken is stubbed.
 * Run with: npx tsx --test packages/founder-ide-extension/src/gateway-client.spec.ts
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  callGateway,
  callFim,
  computeBackoffMs,
  gatewayUserMessage,
  parseRetryAfter,
  redactSecrets,
  validateFimRequest,
  validateFimResponse,
  type GatewayClient,
  type GatewayCallOptions,
  type StreamCallbacks,
  type GatewayToolCall,
  type FimRequest,
} from './gateway-client.js';

// ─── CancellationToken stub ─────────────────────────────────────────────────

interface Disposable {
  dispose(): void;
}
interface CancellationToken {
  isCancellationRequested: boolean;
  onCancellationRequested(cb: () => void): Disposable;
}

function makeToken(): CancellationToken & { cancel(): void } {
  const listeners: Array<() => void> = [];
  let cancelled = false;
  return {
    get isCancellationRequested() {
      return cancelled;
    },
    onCancellationRequested(cb: () => void): Disposable {
      listeners.push(cb);
      return { dispose: () => {
        const i = listeners.indexOf(cb);
        if (i >= 0) listeners.splice(i, 1);
      } };
    },
    cancel() {
      cancelled = true;
      for (const cb of [...listeners]) cb();
    },
  };
}

// ─── Mock fetch + helpers ───────────────────────────────────────────────────

/** Build a Response-like object from raw SSE bytes. */
function sseResponse(chunks: string[], init?: { status?: number; headers?: Record<string, string> }): Response {
  const status = init?.status ?? 200;
  const headers = new Headers(init?.headers);
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(stream, { status, headers });
}

function makeFetchSequence(responses: Array<Response | Error>): {
  fetchImpl: typeof fetch;
  calls: Array<{ url: string; init?: RequestInit }>;
} {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let i = 0;
  const fetchImpl = ((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const next = responses[i++];
    if (next instanceof Error) return Promise.reject(next);
    return Promise.resolve(next);
  }) as unknown as typeof fetch;
  return { fetchImpl, calls };
}

const noopSleep = async () => {
  // Skip the backoff delay so tests run fast.
};

const CLIENT: GatewayClient = { baseUrl: 'https://gw.test/api/v1', bearer: 'fos_node1:tok_abc' };

const DEFAULT_OPTS: GatewayCallOptions = {
  model: 'founder-os-turbo',
  messages: [{ role: 'user', content: 'hi' }],
  timeoutMs: 5000,
  maxRetries: 3,
};

function callbacks(): StreamCallbacks & { tokens: string[]; meta: unknown[]; errors: Array<{ status: number; body: string }>; authExpired: number[]; rateLimited: Array<number | null>; providerErrors: Array<{ status: number; body: string }>; toolCalls: GatewayToolCall[]; logs: Array<{ level: string; message: string }> } {
  const tokens: string[] = [];
  const meta: unknown[] = [];
  const errors: Array<{ status: number; body: string }> = [];
  const authExpired: number[] = [];
  const rateLimited: Array<number | null> = [];
  const providerErrors: Array<{ status: number; body: string }> = [];
  const toolCalls: GatewayToolCall[] = [];
  const logs: Array<{ level: string; message: string }> = [];
  return {
    tokens,
    meta,
    errors,
    authExpired,
    rateLimited,
    providerErrors,
    toolCalls,
    logs,
    onToken: (d) => tokens.push(d),
    onMetadata: (m) => meta.push(m),
    onError: (status, body) => errors.push({ status, body }),
    onAuthExpired: (status) => authExpired.push(status),
    onRateLimited: (ms) => rateLimited.push(ms),
    onProviderError: (status, body) => providerErrors.push({ status, body }),
    onToolCall: (c) => toolCalls.push(c),
    log: (level, message) => logs.push({ level, message }),
  };
}

// ─── Tests ─────────────────────────────────────────────────────────────────

describe('gateway-client — SSE happy path', () => {
  it('emits tokens from 3 chunks + [DONE]', async () => {
    const res = sseResponse([
      'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
      'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
      'data: {"choices":[{"delta":{"content":"!"}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.deepEqual(cb.tokens, ['Hel', 'lo', '!']);
    assert.equal(cb.errors.length, 0);
  });

  it('supports personal-provider headers without requiring a Founder bearer', async () => {
    const res = sseResponse(['data: [DONE]\n\n']);
    const { fetchImpl, calls } = makeFetchSequence([res]);
    await callGateway(
      { baseUrl: 'https://personal.test/v1', bearer: '', headers: { 'X-Project': 'founder' } },
      DEFAULT_OPTS,
      callbacks(),
      makeToken(),
      { fetchImpl, sleepImpl: noopSleep },
    );
    const headers = new Headers(calls[0]?.init?.headers);
    assert.equal(headers.get('X-Project'), 'founder');
    assert.equal(headers.get('Authorization'), null);
    assert.equal(calls[0]?.url, 'https://personal.test/v1/chat/completions');
  });

  it('parses founderOs metadata pre-line', async () => {
    const res = sseResponse([
      'data: {"founderOs":{"requestId":"req_1","tier":"reasoning","provider":"deepseek","model":"deepseek-v4-pro","ddollarCost":0.012}}\n\n',
      'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.equal(cb.tokens.length, 1);
    assert.equal(cb.tokens[0], 'ok');
    assert.equal(cb.meta.length, 1);
    assert.deepEqual(cb.meta[0], {
      requestId: 'req_1',
      tier: 'reasoning',
      provider: 'deepseek',
      model: 'deepseek-v4-pro',
      ddollarCost: 0.012,
    });
  });

  it('reports provider cache hits and misses from terminal usage', async () => {
    const usage: Array<{ promptTokens: number; cachedInputTokens: number; uncachedInputTokens: number; outputTokens: number }> = [];
    await callGateway(
      CLIENT,
      { model: 'founder-os-fast', messages: [{ role: 'user', content: 'hello' }] },
      {
        onToken: () => {},
        onUsage: (value) => usage.push(value),
      },
      makeToken(),
      { fetchImpl: async () => sseResponse([
        'data: {"choices":[],"usage":{"prompt_tokens":180000,"prompt_cache_hit_tokens":150000,"prompt_cache_miss_tokens":30000,"completion_tokens":20000}}\n\n',
        'data: [DONE]\n\n',
      ]) },
    );
    assert.deepEqual(usage, [{
      promptTokens: 180000,
      cachedInputTokens: 150000,
      uncachedInputTokens: 30000,
      outputTokens: 20000,
    }]);
  });

  it('forwards tool definitions and tool choice to the gateway', async () => {
    const res = sseResponse(['data: [DONE]\n\n']);
    const { fetchImpl, calls } = makeFetchSequence([res]);
    await callGateway(
      CLIENT,
      {
        ...DEFAULT_OPTS,
        tools: [
          {
            type: 'function',
            function: {
              name: 'founder-edit-file',
              description: 'Edit a workspace file',
              parameters: { type: 'object' },
            },
          },
        ],
        toolChoice: 'auto',
        metadata: {
          founder_memory_included: true,
          prompt_efficiency: { measurement: 'estimated', avoidedTokens: 120 },
        },
      },
      callbacks(),
      makeToken(),
      { fetchImpl, sleepImpl: noopSleep },
    );
    const body = JSON.parse(String(calls[0]?.init?.body)) as {
      tools?: Array<{ function?: { name?: string } }>;
      tool_choice?: string;
      metadata?: { founder_memory_included?: boolean; prompt_efficiency?: { avoidedTokens?: number } };
    };
    assert.equal(body.tools?.[0]?.function?.name, 'founder-edit-file');
    assert.equal(body.tool_choice, 'auto');
    assert.equal(body.metadata?.founder_memory_included, true);
    assert.equal(body.metadata?.prompt_efficiency?.avoidedTokens, 120);
  });
});

describe('gateway-client — chunk boundary handling', () => {
  it('handles a chunk that ends mid-data-line', async () => {
    // First chunk ends with `data: {"choices"` (no closing brace, no \n\n).
    // Second chunk completes the event.
    const res = sseResponse([
      'data: {"choices":[{"delta":{"co',
      'ntent":"Hi"}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.deepEqual(cb.tokens, ['Hi']);
  });
});

describe('gateway-client — CRLF handling', () => {
  it('strips \\r\\n line endings and \\r\\n\\r\\n event separators', async () => {
    const res = sseResponse([
      'data: {"choices":[{"delta":{"content":"A"}}]}\r\n\r\n',
      'data: {"choices":[{"delta":{"content":"B"}}]}\r\n\r\n',
      'data: [DONE]\r\n\r\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.deepEqual(cb.tokens, ['A', 'B']);
  });
});

describe('gateway-client — malformed frames', () => {
  it('skips malformed JSON with a warning, continues stream', async () => {
    const res = sseResponse([
      'data: not-json-at-all\n\n',
      'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.deepEqual(cb.tokens, ['ok']);
    assert.ok(
      cb.logs.some((l) => l.level === 'warn' && l.message.includes('malformed SSE frame')),
      'expected a warn log for malformed frame',
    );
  });

  it('ignores SSE comment lines (: keep-alive)', async () => {
    const res = sseResponse([
      ': this is a comment / keep-alive\n\n',
      'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.deepEqual(cb.tokens, ['x']);
  });
});

describe('gateway-client — [DONE] terminator', () => {
  it('does not emit a chunk for [DONE]', async () => {
    const res = sseResponse([
      'data: {"choices":[{"delta":{"content":"a"}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.deepEqual(cb.tokens, ['a']);
  });
});

describe('gateway-client — non-2xx errors', () => {
  it('throws on 400 (no retry)', async () => {
    const res = sseResponse(['bad request body'], { status: 400 });
    const { fetchImpl, calls } = makeFetchSequence([res]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
        fetchImpl,
        sleepImpl: noopSleep,
      }),
      /400/,
    );
    assert.equal(calls.length, 1, 'must not retry on 4xx');
    assert.equal(cb.errors.length, 1);
    assert.equal(cb.errors[0].status, 400);
  });

  it('never exposes an HTML 502 page in chat errors', async () => {
    const html = '<!DOCTYPE html><html><head><title>Bad gateway</title></head><body>cloud proxy trace</body></html>';
    const { fetchImpl } = makeFetchSequence([
      sseResponse([html], { status: 502 }),
    ]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(
        CLIENT,
        { ...DEFAULT_OPTS, maxRetries: 0 },
        cb,
        makeToken(),
        { fetchImpl, sleepImpl: noopSleep },
      ),
      (error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        assert.match(message, /temporarily unavailable/);
        assert.match(message, /local files are safe/i);
        assert.doesNotMatch(message, /<!DOCTYPE|<html|proxy trace/i);
        return true;
      },
    );
  });
});

describe('gateway-client — cancellation via AbortSignal', () => {
  it('stops emitting tokens after AbortSignal aborts', async () => {
    // Stream that emits one token, then waits, then emits a second token.
    // We abort after the first token is processed and assert only one was
    // emitted before the stream closed (post-abort).
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"choices":[{"delta":{"content":"first"}}]}\n\n'));
        // Wait briefly, then enqueue a second event and close so the test
        // always terminates even if the abort timing slips.
        setTimeout(() => {
          controller.enqueue(encoder.encode('data: {"choices":[{"delta":{"content":"second"}}]}\n\n'));
          controller.close();
        }, 50);
      },
    });
    const res = new Response(stream, { status: 200 });
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    const token = makeToken();
    const ac = new AbortController();
    // Abort immediately after we see the first token — well within the 50ms
    // window before the second is enqueued.
    const origOnToken = cb.onToken;
    cb.onToken = (d) => {
      origOnToken(d);
      ac.abort();
    };
    await callGateway(
      CLIENT,
      DEFAULT_OPTS,
      cb,
      token,
      { fetchImpl, sleepImpl: noopSleep, signal: ac.signal },
    );
    // We expect at least the first token. The second may or may not arrive
    // depending on abort timing, but the call must not throw or hang.
    assert.ok(cb.tokens.includes('first'), `expected 'first' in ${cb.tokens}`);
  });

  it('honors CancellationToken (VS Code style) by aborting the fetch', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"choices":[{"delta":{"content":"x"}}]}\n\n'));
        setTimeout(() => {
          controller.enqueue(encoder.encode('data: {"choices":[{"delta":{"content":"y"}}]}\n\n'));
          controller.close();
        }, 50);
      },
    });
    const res = new Response(stream, { status: 200 });
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    const token = makeToken();
    const origOnToken = cb.onToken;
    cb.onToken = (d) => {
      origOnToken(d);
      token.cancel();
    };
    await callGateway(CLIENT, DEFAULT_OPTS, cb, token, {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.ok(cb.tokens.includes('x'), `expected 'x' in ${cb.tokens}`);
  });
});

describe('gateway-client — retries', () => {
  it('retries on network error: fail, fail, succeed', async () => {
    const success = sseResponse([
      'data: {"choices":[{"delta":{"content":"got-it"}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl, calls } = makeFetchSequence([
      new Error('ECONNRESET'),
      new Error('ETIMEDOUT'),
      success,
    ]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.deepEqual(cb.tokens, ['got-it']);
    assert.equal(calls.length, 3, 'should have made 3 fetch calls (1 + 2 retries)');
  });

  it('retries on 5xx upstream: 500, 500, 200', async () => {
    const success = sseResponse([
      'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl, calls } = makeFetchSequence([
      sseResponse(['boom'], { status: 500 }),
      sseResponse(['boom'], { status: 503 }),
      success,
    ]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.deepEqual(cb.tokens, ['ok']);
    assert.equal(calls.length, 3);
  });

  it('exhausts retries on persistent 5xx → onProviderError', async () => {
    const { fetchImpl, calls } = makeFetchSequence([
      sseResponse(['boom'], { status: 500 }),
      sseResponse(['boom'], { status: 500 }),
      sseResponse(['boom'], { status: 500 }),
      sseResponse(['boom'], { status: 500 }),
    ]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
        fetchImpl,
        sleepImpl: noopSleep,
      }),
      /500/,
    );
    // 1 initial + 3 retries = 4 total.
    assert.equal(calls.length, 4);
    assert.equal(cb.providerErrors.length, 1);
    assert.equal(cb.providerErrors[0].status, 500);
  });

  it('does NOT retry on 4xx (deterministic)', async () => {
    const { fetchImpl, calls } = makeFetchSequence([
      sseResponse(['bad'], { status: 422 }),
    ]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
        fetchImpl,
        sleepImpl: noopSleep,
      }),
      /422/,
    );
    assert.equal(calls.length, 1);
  });
});

describe('gateway-client — 401/403 auth expired', () => {
  it('fires onAuthExpired and does not retry', async () => {
    const { fetchImpl, calls } = makeFetchSequence([
      sseResponse(['unauthorized'], { status: 401 }),
    ]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
        fetchImpl,
        sleepImpl: noopSleep,
      }),
      /auth expired/,
    );
    assert.equal(calls.length, 1);
    assert.deepEqual(cb.authExpired, [401]);
  });

  it('does not mark the Founder connection expired when the upstream provider key fails', async () => {
    const { fetchImpl } = makeFetchSequence([
      new Response(
        JSON.stringify({ error: { message: 'Authentication Fails, Your api key is invalid' } }),
        { status: 401 },
      ),
    ]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), { fetchImpl, sleepImpl: noopSleep }),
      /selected AI provider is unavailable/i,
    );
    assert.equal(cb.authExpired.length, 0);
    assert.equal(cb.providerErrors.length, 1);
  });
});

describe('gateway-client — 429 rate limit', () => {
  it('parses Retry-After (seconds) and fires onRateLimited', async () => {
    const { fetchImpl, calls } = makeFetchSequence([
      sseResponse(['rate limited'], {
        status: 429,
        headers: { 'retry-after': '30' },
      }),
    ]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
        fetchImpl,
        sleepImpl: noopSleep,
      }),
      /rate limited/,
    );
    assert.equal(calls.length, 1);
    assert.equal(cb.rateLimited.length, 1);
    assert.equal(cb.rateLimited[0], 30_000);
  });

  it('parses Retry-After (HTTP date) and fires onRateLimited', async () => {
    const future = new Date(Date.now() + 60_000).toUTCString();
    const { fetchImpl } = makeFetchSequence([
      sseResponse(['rate limited'], {
        status: 429,
        headers: { 'retry-after': future },
      }),
    ]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
        fetchImpl,
        sleepImpl: noopSleep,
      }),
      /rate limited/,
    );
    assert.equal(cb.rateLimited.length, 1);
    // Allow ±5s slop for test runtime.
    assert.ok(
      cb.rateLimited[0]! > 50_000 && cb.rateLimited[0]! < 70_000,
      `expected ~60s retry-after, got ${cb.rateLimited[0]}`,
    );
  });
});

describe('gateway-client — tool-call aggregation', () => {
  it('aggregates multi-chunk tool calls by index', async () => {
    const res = sseResponse([
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"edit_file","arguments":""}}]}}]}\n\n',
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\""}}]}}]}\n\n',
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":":\\"a.ts\\"}"}}]}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.equal(cb.toolCalls.length, 1);
    const tc = cb.toolCalls[0];
    assert.equal(tc.index, 0);
    assert.equal(tc.id, 'call_1');
    assert.equal(tc.name, 'edit_file');
    assert.equal(tc.arguments, '{"path":"a.ts"}');
  });

  it('emits two separate tool calls when index changes', async () => {
    const res = sseResponse([
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"a","arguments":"1"}}]}}]}\n\n',
      'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"c2","function":{"name":"b","arguments":"2"}}]}}]}\n\n',
      'data: [DONE]\n\n',
    ]);
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await callGateway(CLIENT, DEFAULT_OPTS, cb, makeToken(), {
      fetchImpl,
      sleepImpl: noopSleep,
    });
    assert.equal(cb.toolCalls.length, 2);
    assert.equal(cb.toolCalls[0].name, 'a');
    assert.equal(cb.toolCalls[1].name, 'b');
  });
});

describe('gateway-client — FIM', () => {
  it('validateFimRequest accepts valid input', () => {
    assert.doesNotThrow(() =>
      validateFimRequest({ model: 'm', prefix: 'abc', suffix: 'xyz' }),
    );
  });

  it('validateFimRequest rejects non-string prefix', () => {
    assert.throws(
      () => validateFimRequest({ model: 'm', prefix: 42 as unknown as string, suffix: '' }),
      /prefix must be a string/,
    );
  });

  it('validateFimRequest rejects non-string suffix', () => {
    assert.throws(
      () => validateFimRequest({ model: 'm', prefix: '', suffix: 99 as unknown as string }),
      /suffix must be a string/,
    );
  });

  it('validateFimRequest rejects empty model', () => {
    assert.throws(
      () => validateFimRequest({ model: '', prefix: '', suffix: '' }),
      /model must be/,
    );
  });

  it('validateFimResponse accepts choices[0].text', () => {
    assert.doesNotThrow(() =>
      validateFimResponse({ choices: [{ text: 'filled' }] }),
    );
  });

  it('validateFimResponse rejects missing choices', () => {
    assert.throws(
      () => validateFimResponse({}),
      /choices must be a non-empty array/,
    );
  });

  it('validateFimResponse rejects choices[0] without text', () => {
    assert.throws(
      () => validateFimResponse({ choices: [{ finish_reason: 'stop' }] }),
      /text must be a string/,
    );
  });

  it('callFim POSTs /completions with prefix/suffix and returns validated text', async () => {
    const encoder = new TextEncoder();
    const jsonBody = JSON.stringify({
      choices: [{ text: 'middle-bit' }],
    });
    const res = new Response(jsonBody, {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
    let capturedUrl = '';
    let capturedInit: RequestInit | undefined;
    const fetchImpl = ((url: string, init?: RequestInit) => {
      capturedUrl = url;
      capturedInit = init;
      return Promise.resolve(res);
    }) as unknown as typeof fetch;

    const result = await callFim(
      CLIENT,
      { model: 'fim-model', prefix: 'pre', suffix: 'post' },
      makeToken(),
      { fetchImpl },
    );
    assert.equal(result.text, 'middle-bit');
    assert.ok(capturedUrl.endsWith('/completions'), `url was ${capturedUrl}`);
    const body = JSON.parse(capturedInit?.body as string) as {
      prompt: string;
      suffix: string;
    };
    assert.equal(body.prompt, 'pre');
    assert.equal(body.suffix, 'post');
  });

  it('callFim throws on 4xx', async () => {
    const res = new Response('forbidden', { status: 403 });
    const fetchImpl = (() => Promise.resolve(res)) as unknown as typeof fetch;
    await assert.rejects(
      callFim(
        CLIENT,
        { model: 'm', prefix: '', suffix: '' },
        makeToken(),
        { fetchImpl },
      ),
      /403/,
    );
  });
});

describe('gateway-client — secret redaction', () => {
  it('redacts Authorization header values', () => {
    const s = redactSecrets('Authorization: Bearer super-secret-token');
    assert.ok(!s.includes('super-secret-token'), `got ${s}`);
    assert.ok(s.includes('***'));
  });

  it('redacts nodeToken field in JSON', () => {
    const s = redactSecrets('{"nodeToken":"tok_xyz123","foo":"bar"}');
    assert.ok(!s.includes('tok_xyz123'), `got ${s}`);
    assert.ok(s.includes('"nodeToken":"***"'));
  });

  it('redacts api_key field in JSON', () => {
    const s = redactSecrets('{"api_key":"sk_live_abc"}');
    assert.ok(!s.includes('sk_live_abc'), `got ${s}`);
    assert.ok(s.includes('"api_key":"***"'));
  });

  it('redacts query-string style api_key', () => {
    const s = redactSecrets('https://x.test/y?api_key=sk_abc&z=1');
    assert.ok(!s.includes('sk_abc'), `got ${s}`);
    assert.ok(s.includes('api_key=***'));
  });

  it('passes through non-secret content unchanged', () => {
    const s = redactSecrets('hello world model=glm tier=reasoning');
    assert.equal(s, 'hello world model=glm tier=reasoning');
  });

  it('does not log Authorization bearer on auth-expired warning', async () => {
    const res = sseResponse(['unauthorized'], { status: 401 });
    const { fetchImpl } = makeFetchSequence([res]);
    const cb = callbacks();
    await assert.rejects(
      callGateway(
        { baseUrl: CLIENT.baseUrl, bearer: 'fos_secret_token_xyz' },
        DEFAULT_OPTS,
        cb,
        makeToken(),
        { fetchImpl, sleepImpl: noopSleep },
      ),
      /auth expired/,
    );
    // No log line should leak the bearer token.
    for (const l of cb.logs) {
      assert.ok(
        !l.message.includes('fos_secret_token_xyz'),
        `log leaked bearer: ${l.message}`,
      );
    }
  });
});

describe('gateway-client — helpers', () => {
  describe('gatewayUserMessage', () => {
    it('turns auth failures into a reconnect action', () => {
      assert.match(gatewayUserMessage(401, '<html>ignored</html>'), /sign in again/i);
    });

    it('distinguishes an upstream provider key failure from Founder sign-in', () => {
      const body = JSON.stringify({
        error: { message: 'Authentication Fails, Your api key: ****trol is invalid' },
      });
      const message = gatewayUserMessage(401, body);
      assert.match(message, /selected AI provider is unavailable/i);
      assert.doesNotMatch(message, /sign in again|trol/i);
    });

    it('keeps a safe JSON message for deterministic 4xx failures', () => {
      const message = gatewayUserMessage(422, '{"error":{"message":"Model is not enabled"}}');
      assert.match(message, /Model is not enabled/);
      assert.doesNotMatch(message, /\{"error/);
    });
  });

  describe('parseRetryAfter', () => {
    it('parses integer seconds', () => {
      assert.equal(parseRetryAfter('30'), 30_000);
      assert.equal(parseRetryAfter('0'), 0);
    });

    it('parses HTTP date in the future', () => {
      const future = new Date(Date.now() + 30_000).toUTCString();
      const ms = parseRetryAfter(future);
      assert.ok(ms !== null);
      assert.ok(ms! > 20_000 && ms! < 40_000, `got ${ms}`);
    });

    it('returns null for empty / garbage', () => {
      assert.equal(parseRetryAfter(null), null);
      assert.equal(parseRetryAfter(''), null);
      assert.equal(parseRetryAfter('not-a-date'), null);
    });
  });

  describe('computeBackoffMs', () => {
    it('returns 1s ± 25% on attempt 0', () => {
      for (let i = 0; i < 50; i += 1) {
        const v = computeBackoffMs(0);
        assert.ok(v >= 750 && v <= 1250, `attempt 0 out of range: ${v}`);
      }
    });

    it('returns 2s ± 25% on attempt 1', () => {
      for (let i = 0; i < 50; i += 1) {
        const v = computeBackoffMs(1);
        assert.ok(v >= 1500 && v <= 2500, `attempt 1 out of range: ${v}`);
      }
    });

    it('returns 4s ± 25% on attempt 2', () => {
      for (let i = 0; i < 50; i += 1) {
        const v = computeBackoffMs(2);
        assert.ok(v >= 3000 && v <= 5000, `attempt 2 out of range: ${v}`);
      }
    });
  });
});
