import { Injectable, Logger, Optional, forwardRef, Inject } from '@nestjs/common';
import { MemoryEngineService } from '../memory-engine/memory-engine.service';
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
 *
 * Also builds the memory-context prefix injected into the system prompt before
 * each AI call (kernel §3 — Memory Engine). The Memory Engine is injected via a
 * forwardRef because MemoryEngineModule pulls in FounderNodeModule, which sits
 * on the far side of the kernel boundary and may transitively reach back into
 * the AI runtime. The injection is optional so the context builder still
 * constructs in unit tests that don't wire the full module graph.
 */
@Injectable()
export class ContextBuilderService {
  private readonly logger = new Logger(ContextBuilderService.name);

  constructor(
    @Optional() @Inject(forwardRef(() => MemoryEngineService))
    private readonly memory?: MemoryEngineService,
  ) {}

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

  // ─── Memory Engine integration (kernel §3) ────────────────────────────────

  /**
   * Build a compact memory-context snippet to prepend to the system prompt.
   * Pulls founder-level preferences (cross-project) and, when a projectId is
   * supplied, project-level operational intelligence. Best-effort: any error
   * or empty store yields an empty string so AI calls never break on a memory
   * hiccup.
   *
   * Gated by AI_RUNTIME_MEMORY_CONTEXT !== 'false' so it can be disabled in
   * environments that don't want the extra DB reads on every call.
   */
  async buildMemoryContext(userId: string, projectId?: string | null): Promise<string> {
    if (!this.memory) return '';
    if (process.env.AI_RUNTIME_MEMORY_CONTEXT === 'false') return '';

    const lines: string[] = [];
    try {
      const founderMemory = await this.memory.query({
        store: 'founder',
        scope: userId,
        limit: 20,
      });
      if (founderMemory.length > 0) {
        lines.push('Founder memory (preferences, durable across projects):');
        for (const entry of founderMemory) {
          lines.push(`  - ${entry.key}: ${this.renderValue(entry.value)}`);
        }
      }
    } catch (err) {
      this.logger.debug(
        `founder memory context fetch failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    if (projectId) {
      try {
        const projectMemory = await this.memory.query({
          store: 'project',
          scope: projectId,
          limit: 20,
        });
        if (projectMemory.length > 0) {
          lines.push(`Project memory (${projectId}):`);
          for (const entry of projectMemory) {
            lines.push(`  - ${entry.key}: ${this.renderValue(entry.value)}`);
          }
        }
      } catch (err) {
        this.logger.debug(
          `project memory context fetch failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    if (lines.length === 0) return '';
    return `# Memory context\n${lines.join('\n')}\n`;
  }

  /**
   * Async variant of prepareRequest that additionally injects memory context
   * into the system prompt. Callers that want memory injection should use this
   * instead of the sync prepareRequest (which is kept for backward compat).
   */
  async prepareRequestWithMemory(request: AiRuntimeRequest): Promise<AiRuntimeRequest> {
    const memoryCtx = await this.buildMemoryContext(request.userId, request.projectId);
    const system = memoryCtx ? `${memoryCtx}\n${request.system}` : request.system;
    return this.prepareRequest({ ...request, system });
  }

  private renderValue(value: unknown): string {
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
}
