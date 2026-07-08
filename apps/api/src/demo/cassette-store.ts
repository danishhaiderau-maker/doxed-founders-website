/**
 * Cassette store — replay/capture layer for the demo harness.
 *
 * Mode (read from process.env.DEMO_CASSETTE_MODE):
 *   - 'replay' (default): read-only. Returns undefined on miss.
 *   - 'capture': writes every set() to disk; get() still reads first.
 *
 * Cassette dir defaults to <repo-root>/cassettes and is structured:
 *   cassettes/<bucket>/<key>.json
 *
 * Every entry shape:
 *   { key, capturedAt, mode, request?, response }
 *
 * This module is inert unless DEMO_MODE_ENABLED=true. The bot's own
 * Python-side shim mirrors this logic (services/btc-conservative-agent/
 * demo_mode.py) and uses the same file layout so capture/replay stay
 * deterministic across both runtimes.
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

export type CassetteMode = 'replay' | 'capture';

export type CassetteEntry<T = unknown> = {
  key: string;
  capturedAt: string;
  mode: CassetteMode;
  request?: Record<string, unknown>;
  response: T;
};

const REPO_ROOT = resolve(__dirname, '..', '..');
const DEFAULT_DIR = process.env.DEMO_CASSETTE_DIR
  ? resolve(process.env.DEMO_CASSETTE_DIR)
  : join(REPO_ROOT, 'cassettes');

export function cassetteDir(): string {
  return DEFAULT_DIR;
}

export function mode(): CassetteMode {
  const raw = (process.env.DEMO_CASSETTE_MODE || 'replay').trim().toLowerCase();
  if (raw === 'capture') return 'capture';
  return 'replay';
}

export function isCapture(): boolean {
  return mode() === 'capture';
}

export function isDemoMode(): boolean {
  return process.env.DEMO_MODE_ENABLED === 'true';
}

/**
 * Stable hash for AI cassettes. Must match the Python shim in demo_mode.py.
 * Hash = sha1(model + '|' + temperature + '|' + promptPrefix(256 chars)).
 */
export function deepseekKey(model: string, temperature: number, promptPrefix: string): string {
  const prefix = (promptPrefix ?? '').slice(0, 256);
  return createHash('sha1')
    .update(`${model}|${temperature}|${prefix}`)
    .digest('hex')
    .slice(0, 24);
}

function pathFor(bucket: string, key: string): string {
  const safeBucket = bucket.replace(/[^a-z0-9_-]/gi, '_');
  const safeKey = key.replace(/[^a-z0-9_-]/gi, '_');
  return join(DEFAULT_DIR, safeBucket, `${safeKey}.json`);
}

export function get<T = unknown>(bucket: string, key: string): CassetteEntry<T> | null {
  const path = pathFor(bucket, key);
  if (!existsSync(path)) return null;
  try {
    const raw = readFileSync(path, 'utf8');
    return JSON.parse(raw) as CassetteEntry<T>;
  } catch {
    return null;
  }
}

export function set<T = unknown>(
  bucket: string,
  key: string,
  response: T,
  request?: Record<string, unknown>,
): CassetteEntry<T> {
  const entry: CassetteEntry<T> = {
    key,
    capturedAt: new Date().toISOString(),
    mode: mode(),
    request,
    response,
  };
  const path = pathFor(bucket, key);
  mkdirSync(join(path, '..'), { recursive: true });
  writeFileSync(path, JSON.stringify(entry, null, 2) + '\n', 'utf8');
  return entry;
}

/**
 * Replay-or-capture wrapper for one-off external calls.
 * In replay mode with a miss: returns the provided fallback (or null) and
 * does NOT write. In capture mode: writes and returns.
 */
export function replayOrCapture<T>(
  bucket: string,
  key: string,
  capture: () => T | Promise<T>,
  options: { fallback?: T; request?: Record<string, unknown> } = {},
): Promise<T | null> {
  const existing = get<T>(bucket, key);
  if (existing) return Promise.resolve(existing.response);
  if (isCapture()) {
    const result = capture();
    if (result instanceof Promise) {
      return result.then((r) => {
        set(bucket, key, r, options.request);
        return r;
      });
    }
    set(bucket, key, result, options.request);
    return Promise.resolve(result);
  }
  return Promise.resolve(options.fallback ?? null);
}
