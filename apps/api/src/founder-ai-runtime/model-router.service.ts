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

@Injectable()
export class ModelRouterService {
  constructor(private readonly brainProviders: FounderBrainProvidersService) {}

  classifyIntent(request: AiRuntimeRequest): AiRuntimeIntent {
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
    const fastModel = cfg.fastModel;
    const codingModel = cfg.codingModel;
    const fastProvider = cfg.fastProvider;
    const codingProvider = cfg.codingProvider;
    const twoModel = cfg.twoModelRoutingEnabled;

    let route: ModelRoute;

    switch (intent) {
      case 'code':
        route = {
          intent,
          providerKey: twoModel ? codingProvider : 'glm',
          model: codingModel,
          tier: 'code',
        };
        break;
      case 'reasoning':
        route = {
          intent,
          providerKey: twoModel ? codingProvider : 'deepseek',
          model: twoModel && codingProvider === 'glm' ? codingModel : fastModel,
          tier: 'reasoning',
        };
        break;
      case 'summarize':
        route = {
          intent,
          providerKey: twoModel ? fastProvider : 'glm',
          model: fastModel,
          tier: 'fast',
        };
        break;
      case 'social_draft':
      case 'simple_qa':
        route = {
          intent,
          providerKey: twoModel ? fastProvider : 'deepseek',
          model: fastModel,
          tier: 'fast',
        };
        break;
      default:
        route = this.defaultRouteForSection(request.section, intent, twoModel, fastProvider, fastModel);
        break;
    }

    return this.brainProviders.resolveRouteProviders(route);
  }

  private defaultRouteForSection(
    section: AiRuntimeSection,
    intent: AiRuntimeIntent,
    twoModel: boolean,
    fastProvider: string,
    fastModel: string,
  ): ModelRoute {
    if (section === 'wall_summarizer') {
      return {
        intent,
        providerKey: twoModel ? fastProvider : 'glm',
        model: fastModel,
        tier: 'fast',
      };
    }
    return {
      intent,
      providerKey: twoModel ? fastProvider : 'deepseek',
      model: fastModel,
      tier: 'fast',
    };
  }
}
