import { Injectable } from '@nestjs/common';
import { classifyFounderBrainTask } from '@dcf/utils';
import { FounderBrainProvidersService } from './founder-brain-providers.service';
import type {
  AiRuntimeIntent,
  AiRuntimeRequest,
  ModelRoute,
} from './founder-ai-runtime.types';

const CODE_PATTERN =
  /\b(code|typescript|javascript|python|rust|implement|refactor|debug|function|component|api route|nestjs|react|sql|schema|prisma)\b/i;
const REASONING_PATTERN =
  /\b(why|explain|analyze|compare|tradeoff|strategy|architecture|regulatory|compliance|tokenomics|roadmap|plan)\b/i;
const SIMPLE_PATTERN =
  /\b(what is|how do i|status|hello|thanks|yes|no|list|show me)\b/i;

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

    switch (intent) {
      case 'code':
      case 'reasoning':
        return {
          intent,
          providerKey: 'deepseek',
          model: cfg.deepseekCodingModel,
          tier: intent === 'code' ? 'code' : 'reasoning',
        };
      case 'summarize':
      case 'social_draft':
      case 'simple_qa':
        return {
          intent,
          providerKey: 'deepseek',
          model: cfg.deepseekFastModel,
          tier: 'fast',
        };
      default:
        return this.defaultRoute(intent, cfg);
    }
  }

  getFallbackRoute(request: AiRuntimeRequest): ModelRoute {
    const intent = this.classifyIntent(request);
    const cfg = this.brainProviders.getSyncConfig();

    switch (intent) {
      case 'code':
      case 'reasoning':
      case 'summarize':
      case 'social_draft':
      case 'simple_qa':
        return {
          intent,
          providerKey: 'deepseek',
          model: cfg.deepseekCodingModel,
          tier: intent === 'code' ? 'code' : 'reasoning',
        };
      default:
        return {
          intent,
          providerKey: 'deepseek',
          model: cfg.deepseekCodingModel,
          tier: 'reasoning',
        };
    }
  }

  private defaultRoute(
    intent: AiRuntimeIntent,
    cfg: { deepseekFastModel: string },
  ): ModelRoute {
    return {
      intent,
      providerKey: 'deepseek',
      model: cfg.deepseekFastModel,
      tier: 'fast',
    };
  }
}
