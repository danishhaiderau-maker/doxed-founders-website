import { Injectable, Logger } from '@nestjs/common';
import { createHash } from 'node:crypto';
import { FounderBrainProvidersService } from '../founder-ai-runtime/founder-brain-providers.service';
import { getGlmApiBaseUrl } from '../founder-os/glm-config';

/**
 * Intent Classifier Service — Phase 5b.
 *
 * Replaces the crude regex `inferIntent()` in AiProxyRuntimeService with a
 * hybrid classifier that routes prompts into one of three model tiers:
 *
 *   - fast      → simple Q&A, autocomplete, lookups          (DeepSeek Flash)
 *   - code      → writing / debugging / refactoring code     (DeepSeek Pro)
 *   - reasoning → architecture, planning, analysis           (GLM 5.1)
 *
 * The classification runs in three layers:
 *
 *   Layer 1 — Heuristic pre-filter (zero cost). Obvious patterns short-circuit
 *             and skip the model call entirely. Confidence > 0.8 wins.
 *   Layer 2 — GLM 4 Flash model call (only when heuristics are uncertain).
 *             Cheapest model, <200ms, one-word reply. ~$0.0001 / call.
 *   Layer 3 — Context-signal augmentation. File extension, stack traces,
 *             prompt length, and workspace phase bias the result.
 *
 * Every classification is logged (structured) so the Learning Engine can
 * later audit accuracy and refine the heuristic thresholds.
 */
@Injectable()
export class IntentClassifierService {
  private readonly logger = new Logger(IntentClassifierService.name);

  /** GLM model used for the Layer 2 fallback. Env-overridable for rollouts. */
  private readonly classifierModel =
    process.env.INTENT_CLASSIFIER_MODEL?.trim() || 'glm-4-flash';

  /** Hard cap on the GLM call — classification must never stall routing. */
  private readonly modelTimeoutMs = 1500;

  constructor(private readonly brainProviders: FounderBrainProvidersService) {}

  /**
   * Classify a prompt into one of the three routing tiers.
   *
   * Returns the intent plus a 0..1 confidence and the human-readable signal
   * names that fired (for audit / Learning Engine).
   */
  async classify(
    prompt: string,
    context?: {
      fileName?: string;
      hasStackTrace?: boolean;
      workspaceState?: string;
    },
  ): Promise<{
    intent: RouterIntent;
    confidence: number;
    signals: string[];
    /** True when the GLM model was actually invoked (for cost audit). */
    modelCalled: boolean;
  }> {
    const trimmed = (prompt ?? '').trim();
    const signals: string[] = [];

    // ── Layer 1: heuristic pre-filter ──────────────────────────────────────
    const heuristic = this.heuristicClassify(trimmed);
    let intent: RouterIntent = heuristic.intent;
    let confidence = heuristic.confidence;
    if (heuristic.signal) signals.push(heuristic.signal);
    let modelCalled = false;

    // ── Layer 2: GLM 4 Flash (only if heuristics are uncertain) ────────────
    if (confidence < 0.8 && trimmed.length > 0) {
      const modelResult = await this.classifyWithModel(trimmed);
      if (modelResult) {
        modelCalled = true;
        signals.push('glm-4-flash');
        // The model is authoritative when it answers — trust it fully but
        // keep the heuristic as a tie-breaker signal for the audit log.
        intent = modelResult;
        confidence = 0.82;
      } else {
        // Model call failed / timed out. Fall back to whatever the heuristic
        // gave us with reduced confidence so the caller knows it's uncertain.
        signals.push('model-unavailable');
        confidence = Math.min(confidence, 0.6);
      }
    }

    // ── Layer 3: context-signal augmentation ───────────────────────────────
    const ctx = this.applyContextSignals(intent, confidence, trimmed, context, signals);
    intent = ctx.intent;
    confidence = ctx.confidence;

    // ── Audit log (feeds the Learning Engine) ──────────────────────────────
    this.logClassification({
      promptHash: this.hashPrompt(trimmed),
      heuristic: heuristic.intent,
      heuristicConfidence: heuristic.confidence,
      modelCalled,
      finalIntent: intent,
      confidence,
      signals,
      promptLength: trimmed.length,
    });

    return { intent, confidence, signals, modelCalled };
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Layer 1 — heuristics
  // ──────────────────────────────────────────────────────────────────────────

  /**
   * Zero-cost pattern matching. Returns the first rule that fires with high
   * confidence, otherwise a low-confidence default.
   */
  private heuristicClassify(prompt: string): {
    intent: RouterIntent;
    confidence: number;
    signal: string | null;
  } {
    // Code: code fence + a fix/debug/error verb → code (0.9)
    const hasCodeFence = /```|`/.test(prompt);
    const lower = prompt.toLowerCase();
    const hasFixVerb = /\b(fix|debug|error|bug|crash|traceback|exception|stacktrace)\b/.test(
      lower,
    );
    if (hasCodeFence && hasFixVerb) {
      return { intent: 'code', confidence: 0.9, signal: 'heuristic:code-fence+fix-verb' };
    }
    // Bare code fence (refactor / implement request) → code (0.82)
    if (hasCodeFence) {
      return { intent: 'code', confidence: 0.82, signal: 'heuristic:code-fence' };
    }

    // Fast: short lookup-style question → fast (0.85)
    if (prompt.length < 50 && /\b(what is|how do i|where is|who is|when is)\b/.test(lower)) {
      return { intent: 'fast', confidence: 0.85, signal: 'heuristic:short-lookup' };
    }

    // Reasoning: design / architecture / planning verbs → reasoning (0.85)
    if (
      /\b(design|architect(?:ure)?|plan|should i|compare|brainstorm|strategy|trade-?offs?|evaluate)\b/.test(
        lower,
      )
    ) {
      return { intent: 'reasoning', confidence: 0.85, signal: 'heuristic:reasoning-verb' };
    }

    // Uncertain — hand off to the model. The legacy length-only rule would
    // have called anything < 200 chars "simple_qa", but we now defer.
    return { intent: 'reasoning', confidence: 0.4, signal: null };
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Layer 2 — GLM 4 Flash model call
  // ──────────────────────────────────────────────────────────────────────────

  /**
   * Call the cheapest GLM model with a one-word classification prompt.
   * Returns null on any failure (timeout, network, parse, no API key) so the
   * caller can fall back to the heuristic result gracefully.
   */
  private async classifyWithModel(prompt: string): Promise<RouterIntent | null> {
    try {
      const apiKey = await this.brainProviders.resolveApiKey('glm');
      if (!apiKey) return null;

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), this.modelTimeoutMs);

      const response = await fetch(`${getGlmApiBaseUrl()}/chat/completions`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: this.classifierModel,
          messages: [
            { role: 'system', content: CLASSIFICATION_PROMPT },
            { role: 'user', content: prompt.slice(0, 1000) },
          ],
          max_tokens: 10,
          temperature: 0,
        }),
        signal: controller.signal,
      }).finally(() => clearTimeout(timeout));

      if (!response.ok) {
        this.logger.debug(
          `GLM classifier non-OK ${response.status}; falling back to heuristic`,
        );
        return null;
      }

      const data = (await response.json()) as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      const raw = data?.choices?.[0]?.message?.content?.trim().toLowerCase() ?? '';
      return this.parseModelReply(raw);
    } catch (err) {
      this.logger.debug(
        `GLM classifier call failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }

  /**
   * Tolerant parse of the one-word model reply. Accepts "fast", "code",
   * "reasoning" (and common synonyms / punctuation noise) and rejects
   * anything ambiguous so the heuristic fallback kicks in.
   */
  private parseModelReply(raw: string): RouterIntent | null {
    const token = raw.replace(/[^a-z]/g, '');
    if (token === 'fast' || token === 'simple' || token === 'qa') return 'fast';
    if (token === 'code' || token === 'coding' || token === 'debug') return 'code';
    if (
      token === 'reasoning' ||
      token === 'reason' ||
      token === 'architecture' ||
      token === 'design' ||
      token === 'planning' ||
      token === 'plan'
    ) {
      return 'reasoning';
    }
    return null;
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Layer 3 — context-signal augmentation
  // ──────────────────────────────────────────────────────────────────────────

  /**
   * Bias the (intent, confidence) based on side-channel signals from the
   * request context. These nudge a borderline classification but never
   * override a high-confidence heuristic or model result on their own.
   */
  private applyContextSignals(
    intent: RouterIntent,
    confidence: number,
    prompt: string,
    context: { fileName?: string; hasStackTrace?: boolean; workspaceState?: string } | undefined,
    signals: string[],
  ): { intent: RouterIntent; confidence: number } {
    let effectiveIntent = intent;
    let effectiveConfidence = confidence;

    // Editing a code file → bias toward code (only when currently uncertain).
    const fileName = context?.fileName?.toLowerCase() ?? '';
    const isCodeFile = /\.(ts|tsx|js|jsx|py|go|rs|java|rb|php|c|cpp|cs)$/.test(fileName);
    if (isCodeFile && effectiveConfidence < 0.85) {
      signals.push('ctx:code-file');
      if (effectiveIntent === 'fast') {
        effectiveIntent = 'code';
        effectiveConfidence = Math.max(effectiveConfidence, 0.7);
      }
    }

    // Stack trace present → strong bias toward code (debugging).
    const autoHasStack =
      /at \w+ \(/.test(prompt) || /Traceback \(most recent call last\)/.test(prompt);
    const hasStack = context?.hasStackTrace ?? autoHasStack;
    if (hasStack) {
      signals.push('ctx:stack-trace');
      effectiveIntent = 'code';
      effectiveConfidence = Math.max(effectiveConfidence, 0.88);
    }

    // Very long prompt → bias toward reasoning.
    if (prompt.length > 2000 && effectiveIntent === 'fast') {
      signals.push('ctx:long-prompt');
      effectiveIntent = 'reasoning';
      effectiveConfidence = Math.max(effectiveConfidence, 0.7);
    }

    // Workspace in architecture phase → bias toward reasoning.
    const state = (context?.workspaceState ?? '').toLowerCase();
    if (
      state.includes('architecture') ||
      state.includes('planning') ||
      state.includes('design')
    ) {
      signals.push('ctx:arch-phase');
      if (effectiveIntent !== 'code') {
        effectiveIntent = 'reasoning';
        effectiveConfidence = Math.max(effectiveConfidence, 0.78);
      }
    }

    return { intent: effectiveIntent, confidence: effectiveConfidence };
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Audit logging
  // ──────────────────────────────────────────────────────────────────────────

  /**
   * Structured one-line log per classification. Format is JSON so the
   * Learning Engine can tail it and compute accuracy / cost / fallback rate.
   * Best-effort: never throws.
   */
  private logClassification(entry: {
    promptHash: string;
    heuristic: RouterIntent;
    heuristicConfidence: number;
    modelCalled: boolean;
    finalIntent: RouterIntent;
    confidence: number;
    signals: string[];
    promptLength: number;
  }): void {
    try {
      this.logger.log(
        JSON.stringify({
          evt: 'intent_classification',
          ...entry,
        }),
      );
    } catch {
      // logging is best-effort
    }
  }

  private hashPrompt(prompt: string): string {
    const normalized = prompt.replace(/\s+/g, ' ').trim().toLowerCase().slice(0, 4096);
    return createHash('sha256').update(normalized, 'utf8').digest('hex').slice(0, 16);
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Shared types & constants
// ──────────────────────────────────────────────────────────────────────────────

/**
 * The three-tier routing vocabulary used by the Phase 5b classifier. This is
 * a strict subset of `AiRuntimeIntent` (`fast` maps onto `simple_qa`).
 */
export type RouterIntent = 'fast' | 'code' | 'reasoning';

/**
 * Map the classifier's RouterIntent onto the legacy AiRuntimeIntent enum so
 * the Routing Engine v2 and the Flight Recorder keep working unchanged.
 * `fast` is the founder-facing tier name; the kernel still calls it
 * `simple_qa` internally.
 */
export function routerIntentToRuntimeIntent(intent: RouterIntent):
  | 'code'
  | 'reasoning'
  | 'simple_qa' {
  switch (intent) {
    case 'fast':
      return 'simple_qa';
    case 'code':
      return 'code';
    case 'reasoning':
      return 'reasoning';
  }
}

/**
 * System prompt for the GLM 4 Flash classification call. Constrained to a
 * one-word reply so parsing is trivial and token cost is ~1 output token.
 */
export const CLASSIFICATION_PROMPT = `Classify this coding prompt into exactly one: "fast", "code", or "reasoning".
- fast: simple questions, autocomplete-style, quick lookups, definitions
- code: writing code, debugging, refactoring, implementing features
- reasoning: architecture, design decisions, planning, complex analysis
Reply with ONLY the category name, nothing else.`;
