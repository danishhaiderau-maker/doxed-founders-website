import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { WorkspaceCacheContext } from './workspace-context-state';

export interface SafeResultCacheInput {
  prompt: string;
  model: string;
  context: WorkspaceCacheContext;
}

export interface SafeResultCacheHit {
  text: string;
  createdAt: string;
  estimatedTokensAvoided: number;
  contextHash: string;
}

interface PersistedResult extends SafeResultCacheHit {
  version: 2;
  expiresAt: string;
  model: string;
  workspaceId: string;
}

const DEFAULT_TTL_MS = 24 * 60 * 60_000;
const MAX_RESPONSE_CHARS = 80_000;

export class FounderSafeResultCache {
  constructor(
    private readonly root: string,
    private readonly ttlMs = DEFAULT_TTL_MS,
  ) {}

  get(input: SafeResultCacheInput, now = Date.now()): SafeResultCacheHit | null {
    if (!isSafeReadOnlyPrompt(input.prompt)) return null;
    const file = this.fileFor(input);
    try {
      const parsed = JSON.parse(fs.readFileSync(file, 'utf8')) as Partial<PersistedResult>;
      if (
        parsed.version !== 2
        || typeof parsed.text !== 'string'
        || typeof parsed.createdAt !== 'string'
        || typeof parsed.expiresAt !== 'string'
        || typeof parsed.estimatedTokensAvoided !== 'number'
        || parsed.model !== input.model
        || parsed.workspaceId !== input.context.workspaceId
        || parsed.contextHash !== input.context.contextHash
        || Date.parse(parsed.expiresAt) <= now
      ) {
        fs.rmSync(file, { force: true });
        return null;
      }
      return {
        text: parsed.text,
        createdAt: parsed.createdAt,
        estimatedTokensAvoided: parsed.estimatedTokensAvoided,
        contextHash: parsed.contextHash,
      };
    } catch {
      return null;
    }
  }

  put(
    input: SafeResultCacheInput,
    text: string,
    estimatedTokensAvoided: number,
    now = Date.now(),
  ): boolean {
    const normalizedText = text.trim();
    if (
      !isSafeReadOnlyPrompt(input.prompt)
      || !normalizedText
      || normalizedText.length > MAX_RESPONSE_CHARS
      || containsSensitiveMaterial(normalizedText)
    ) return false;
    const value: PersistedResult = {
      version: 2,
      text: normalizedText,
      createdAt: new Date(now).toISOString(),
      expiresAt: new Date(now + this.ttlMs).toISOString(),
      estimatedTokensAvoided: Math.max(0, Math.round(estimatedTokensAvoided)),
      contextHash: input.context.contextHash,
      model: input.model,
      workspaceId: input.context.workspaceId,
    };
    try {
      fs.mkdirSync(this.root, { recursive: true });
      const file = this.fileFor(input);
      const temp = `${file}.${process.pid}.tmp`;
      fs.writeFileSync(temp, JSON.stringify(value), { encoding: 'utf8', mode: 0o600 });
      fs.rmSync(file, { force: true });
      fs.renameSync(temp, file);
      return true;
    } catch {
      return false;
    }
  }

  invalidateWorkspace(workspaceId: string): number {
    let removed = 0;
    try {
      for (const entry of fs.readdirSync(this.root, { withFileTypes: true })) {
        if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
        const file = path.join(this.root, entry.name);
        try {
          const value = JSON.parse(fs.readFileSync(file, 'utf8')) as Partial<PersistedResult>;
          if (value.workspaceId !== workspaceId) continue;
          fs.rmSync(file, { force: true });
          removed += 1;
        } catch {
          fs.rmSync(file, { force: true });
        }
      }
    } catch {
      return removed;
    }
    return removed;
  }

  private fileFor(input: SafeResultCacheInput): string {
    const key = createHash('sha256').update([
      'founder-safe-result-v1',
      input.context.workspaceId,
      input.context.contextHash,
      input.model,
      semanticReadOnlyKey(input.prompt),
    ].join('\x1e')).digest('hex');
    return path.join(this.root, `${key}.json`);
  }
}

export function isSafeReadOnlyPrompt(prompt: string): boolean {
  const normalized = prompt.toLowerCase().replace(/\s+/g, ' ').trim();
  if (normalized.length < 4 || normalized.length > 2_000) return false;
  if (/\b(?:add|apply|build|change|commit|configure|create|delete|deploy|edit|execute|fix|generate|implement|install|merge|modify|move|publish|remove|rename|replace|restart|rotate|run|ship|update|upgrade|write)\b/.test(normalized)) {
    return false;
  }
  return /^(?:can you |could you |please )?(?:describe|explain|find|how|list|summarize|tell me|what|where|which|who|why)\b/.test(normalized)
    || normalized.endsWith('?');
}

export function semanticReadOnlyKey(prompt: string): string {
  return prompt
    .toLowerCase()
    .replace(/\b(?:can you|could you|please|tell me)\b/g, ' ')
    .replace(/[^a-z0-9_./-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function containsSensitiveMaterial(value: string): boolean {
  return /(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:api[_-]?key|secret|password|private[_-]?key)\s*[:=]\s*[^\s]{8,}|\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{16,})/i.test(value);
}
