import assert from 'node:assert/strict';
import test from 'node:test';
import { Writable } from 'node:stream';
import { pipeAiProxySseResponse } from './ai-proxy-response-stream';

class CaptureResponse extends Writable {
  readonly chunks: Buffer[] = [];
  readonly headers = new Map<string, string>();
  statusCode = 0;

  status(code: number) {
    this.statusCode = code;
    return this;
  }

  setHeader(name: string, value: string) {
    this.headers.set(name.toLowerCase(), value);
    return this;
  }

  json(value: unknown) {
    this.end(JSON.stringify(value));
    return this;
  }

  _write(chunk: Buffer | string, _encoding: BufferEncoding, done: (error?: Error | null) => void) {
    this.chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    done();
  }

  text() {
    return Buffer.concat(this.chunks).toString('utf8');
  }
}

test('SSE response remains open through metadata, content delta, and DONE', async () => {
  const encoder = new TextEncoder();
  const upstreamBody = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode('data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'),
      );
      controller.enqueue(encoder.encode('data: [DONE]\n\n'));
      controller.close();
    },
  });
  const response = new CaptureResponse();

  await pipeAiProxySseResponse({
    res: response as never,
    upstreamBody,
    includeMetadata: true,
    requestId: 'request-test',
    status: 200,
    tier: 'reasoning',
    provider: 'deepseek',
    model: 'deepseek-v4-pro',
    routeCacheLevel: 'hit',
    promptEfficiency: {
      measurement: 'estimated',
      baselineTokens: 100,
      sentTokens: 75,
      avoidedTokens: 25,
      savingsPercent: 25,
      compactedToolResults: 1,
      removedStaleCoordinationBlocks: 0,
      techniques: ['bounded-tool-results'],
    },
  });

  const body = response.text();
  assert.equal(response.statusCode, 200);
  assert.equal(response.headers.get('content-type'), 'text/event-stream');
  assert.match(body, /"founderOs":/);
  assert.match(body, /"routeCacheLevel":"hit"/);
  assert.match(body, /"measurement":"estimated"/);
  assert.match(body, /"baseline":"same-request-full-context-uncached-input"/);
  assert.match(body, /"avoidedUsd":0\.000010875/);
  assert.match(body, /"choices":\[\{"delta":\{"content":"Hello"\}/);
  assert.match(body, /data: \[DONE\]/);
  assert.ok(body.indexOf('"founderOs":') < body.indexOf('"choices":'));
});
