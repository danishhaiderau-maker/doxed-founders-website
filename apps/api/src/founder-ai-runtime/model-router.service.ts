import { Injectable } from '@nestjs/common';
import { classifyFounderBrainTask } from '@dcf/utils';
import { FounderBrainProvidersService } from './founder-brain-providers.service';
import type {
  AiRuntimeIntent,
  AiRuntimeRequest,
  AiRuntimeSection,
  ModelRoute,
} from './founder-ai-runtime.types';

const CODE_PATTERN =
  /\b(code|typescript|javascript|python|rust|implement|refactor|debug|function|component|api route|nestjs|react|sql|schema|prisma)\b/i;
const REASONING_PATTERN =
  /\b(why|explain|analyze|compare|tradeoff|strategy|architecture|regulatory|compliance|tokenomics|roadmap|plan)\b/i;
const SIMPLE_PATTERN =
  /\b(what is|how do i|status|hello|thanks|yes|no|list|show me)\b/i;

// GLM is reserved exclusively for the Second Brain critical-review surface.
// Do NOT route general traffic here — it is cost-prohibitive. DeepSeek carries
// every general intent (code, reasoning, simple_qa). Gemini handles vision
// preprocessing. Only SecondBrainService may call GLM. See
// apps/api/src/second-brain/second-brain.service.ts.
const GENERAL_PROVIDER = 'deepseek' as const;

type FastLane = {
  providerKey: string;
  model: string;
};
type FallbackLane = FastLane;

@Injectable()
export class ModelRouterService {
  constructor(private readonly brainProviders: FounderBrainProvidersService) {}

  classifyIntent(request: AiRuntimeRequest): AiRuntimeIntent {
    if (request.intentOverride) return request.intentOverride;
    const text = `${request.userPrompt} ${request.section}`;
    if (request.section === 'wall_summarizer') return 'summarize';
    if (request.section === 'share_paraphrase' || request.section === 'founder_draft') {
      return 'social_draft';
    }
    const task = request.founderBrainTask ?? classifyFounderBrainTask(request.userPrompt);
    if (task === 'code' || CODE_PATTERN.test(text)) return 'code';
    if (task === 'strategy' || task === 'research' || REASONING_PATTERN.test(text)) {
      return 'reasoning';
    }
    if (SIMPLE_PATTERN.test(text) && text.length < 120) return 'simple_qa';
    return 'unknown';
  }

  route(request: AiRuntimeRequest): ModelRoute {
    const intent = this.classifyIntent(request);
    const cfg = this.brainProviders.getSyncConfig();

    let fast: FastLane;
    let fallback: FallbackLane;
    let tier: ModelRoute['tier'];

    switch (intent) {
      case 'code':
        fast = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekCodingModel };
        fallback = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekCodingModel };
        tier = 'code';
        break;
      case 'reasoning':
        fast = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekCodingModel };
        fallback = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekFastModel };
        tier = 'reasoning';
        break;
      case 'summarize':
      case 'social_draft':
      case 'simple_qa':
        fast = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekFastModel };
        fallback = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekFastModel };
        tier = 'fast';
        break;
      default:
        return this.defaultRoute(intent, request.section, cfg);
    }

    return this.brainProviders.resolveRouteProviders({
      intent,
      providerKey: fast.providerKey,
      model: fast.model,
      tier,
    });
  }

  getFallbackRoute(request: AiRuntimeRequest): ModelRoute {
    const intent = this.classifyIntent(request);
    const cfg = this.brainProviders.getSyncConfig();

    let fallback: FallbackLane;
    let tier: ModelRoute['tier'];

    switch (intent) {
      case 'code':
        fallback = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekCodingModel };
        tier = 'code';
        break;
      case 'reasoning':
        fallback = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekCodingModel };
        tier = 'reasoning';
        break;
      case 'summarize':
      case 'social_draft':
      case 'simple_qa':
        fallback = { providerKey: GENERAL_PROVIDER, model: cfg.deepseekFastModel };
        tier = 'fast';
        break;
      default:
        return this.defaultRoute(intent, request.section, cfg);
    }

    return {
      intent,
      providerKey: fallback.providerKey,
      model: fallback.model,
      tier,
    };
  }

  private defaultRoute(
    intent: AiRuntimeIntent,
    section: AiRuntimeSection,
    cfg: { deepseekFastModel: string; glmFastModel: string },
  ): ModelRoute {
    // GLM must never be the default route for general traffic. The Wall
    // Summarizer previously defaulted to GLM via this branch; it now shares
    // the cheap DeepSeek fast lane with everything else.
    return {
      intent,
      providerKey: GENERAL_PROVIDER,
      model: cfg.deepseekFastModel,
      tier: 'fast',
    };
  }
}