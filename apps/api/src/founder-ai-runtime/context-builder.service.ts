import { Injectable } from '@nestjs/common';
import type { AiRuntimeIntent, AiRuntimeRequest } from './founder-ai-runtime.types';

const DEFAULT_MAX_OUTPUT: Record<AiRuntimeIntent, number> = {
  simple_qa: 512,
  social_draft: 400,
  summarize: 800,
  code: 4096,
  reasoning: 2048,
  unknown: 1024,
};

/**
 * Trims oversized prompts and caps completion tokens per intent.
 * Conservative defaults — quality-first; disable via AI_RUNTIME_CONTEXT_PRUNING=false.
 */
@Injectable()
export class ContextBuilderService {
  isPruningEnabled(): boolean {
    return process.env.AI_RUNTIME_CONTEXT_PRUNING !== 'false';
  }

  get maxSystemChars(): number {
    const raw = Number(process.env.AI_RUNTIME_MAX_SYSTEM_CHARS ?? 12_000);
    return Number.isFinite(raw) && raw >= 2048 ? raw : 12_000;
  }

  get maxUserPromptChars(): number {
    const raw = Number(process.env.AI_RUNTIME_MAX_USER_PROMPT_CHARS ?? 16_000);
    return Number.isFinite(raw) && raw >= 1024 ? raw : 16_000;
  }

  /** Wall summarizer: cap transcript lines before LLM (local deterministic trim). */
  maxWallTranscriptMessages(): number {
    const raw = Number(process.env.AI_RUNTIME_WALL_MAX_MESSAGES ?? 40);
    return Number.isFinite(raw) && raw >= 5 ? raw : 40;
  }

  pruneSystem(system: string): string {
    if (!this.isPruningEnabled() || system.length <= this.maxSystemChars) return system;
    const headBudget = Math.min(4096, Math.floor(this.maxSystemChars * 0.35));
    const tailBudget = this.maxSystemChars - headBudget - 80;
    const head = system.slice(0, headBudget);
    const tail = system.slice(-tailBudget);
    return `${head}\n\n…[context pruned — earlier system blocks omitted]…\n\n${tail}`;
  }

  pruneUserPrompt(prompt: string): string {
    if (!this.isPruningEnabled() || prompt.length <= this.maxUserPromptChars) return prompt;
    const keep = this.maxUserPromptChars - 48;
    return `…[earlier user context truncated]…\n${prompt.slice(-keep)}`;
  }

  /** Apply pruning when runtime is active (caller may gate on AI_RUNTIME_ENABLED). */
  prepareRequest(request: AiRuntimeRequest): AiRuntimeRequest {
    if (!this.isPruningEnabled()) return request;
    return {
      ...request,
      system: this.pruneSystem(request.system),
      userPrompt: this.pruneUserPrompt(request.userPrompt),
    };
  }

  maxOutputTokens(intent: AiRuntimeIntent): number {
    const envKey = `AI_RUNTIME_MAX_OUTPUT_${intent.toUpperCase()}`;
    const perIntent = process.env[envKey]?.trim();
    if (perIntent) {
      const n = Number(perIntent);
      if (Number.isFinite(n) && n > 0) return Math.floor(n);
    }
    const global = Number(process.env.AI_RUNTIME_MAX_OUTPUT_TOKENS ?? 0);
    if (Number.isFinite(global) && global > 0) return Math.floor(global);
    return DEFAULT_MAX_OUTPUT[intent];
  }
}
