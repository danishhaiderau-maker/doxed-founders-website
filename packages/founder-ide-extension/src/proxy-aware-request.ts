import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const STATUS_MARKER = '__FOUNDER_STATUS__:';

export type ProxyAwareRequest = {
  url: string;
  method: 'POST';
  headers: Record<string, string>;
  body: Uint8Array;
  responseContentType?: string;
  timeoutMs: number;
  maxResponseBytes: number;
};

function curlConfigValue(value: string): string {
  return value
    .replaceAll('\\', '\\\\')
    .replaceAll('"', '\\"')
    .replaceAll('\r', '')
    .replaceAll('\n', '');
}

export async function proxyAwareNativeRequest({
  url,
  method,
  headers,
  body,
  responseContentType = 'application/json',
  timeoutMs,
  maxResponseBytes,
}: ProxyAwareRequest): Promise<Response> {
  const tempDir = await mkdtemp(path.join(os.tmpdir(), 'founder-request-'));
  const bodyPath = path.join(tempDir, 'request.bin');
  const executable = process.platform === 'win32' ? 'curl.exe' : 'curl';

  try {
    await writeFile(bodyPath, body);
    const config = [
      `url = "${curlConfigValue(url)}"`,
      `request = "${method}"`,
      ...Object.entries(headers).map(
        ([name, value]) =>
          `header = "${curlConfigValue(name)}: ${curlConfigValue(value)}"`,
      ),
      `data-binary = "@${curlConfigValue(bodyPath)}"`,
      'silent',
      'show-error',
      `max-time = ${Math.ceil(timeoutMs / 1_000)}`,
      'connect-timeout = 15',
      `write-out = "${STATUS_MARKER}%{http_code}"`,
    ].join('\n');

    const output = await new Promise<string>((resolve, reject) => {
      const child = spawn(executable, ['--config', '-'], {
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      const stdout: Buffer[] = [];
      let outputBytes = 0;
      let settled = false;
      const finish = (callback: () => void) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        callback();
      };
      const timer = setTimeout(() => {
        child.kill();
        finish(() =>
          reject(new Error('Founder native request transport timed out.')),
        );
      }, timeoutMs + 5_000);

      child.stdout.on('data', (chunk: Buffer) => {
        outputBytes += chunk.byteLength;
        if (outputBytes > maxResponseBytes) {
          child.kill();
          finish(() =>
            reject(new Error('Founder native response was too large.')),
          );
          return;
        }
        stdout.push(chunk);
      });
      child.stderr.resume();
      child.on('error', () => {
        finish(() =>
          reject(new Error('Founder native request transport is unavailable.')),
        );
      });
      child.on('close', (code) => {
        finish(() => {
          if (code !== 0) {
            reject(
              new Error(
                'Founder native request transport could not reach its service.',
              ),
            );
            return;
          }
          resolve(Buffer.concat(stdout).toString('utf8'));
        });
      });
      child.stdin.end(config);
    });

    const marker = output.lastIndexOf(STATUS_MARKER);
    if (marker < 0) {
      throw new Error('Founder native request returned an invalid response.');
    }
    const status = Number.parseInt(
      output.slice(marker + STATUS_MARKER.length).trim(),
      10,
    );
    if (!Number.isInteger(status) || status < 100 || status > 599) {
      throw new Error('Founder native request returned an invalid status.');
    }
    return new Response(output.slice(0, marker), {
      status,
      headers: { 'Content-Type': responseContentType },
    });
  } finally {
    await rm(tempDir, { recursive: true, force: true }).catch(() => undefined);
  }
}
