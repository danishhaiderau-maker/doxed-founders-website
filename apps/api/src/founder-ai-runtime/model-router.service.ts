import { Injectable } from '@nestjs/common';
import { classifyFounderBrainTask } from '@dcf/utils';
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
    const fastModel = process.env.AI_RUNTIME_FAST_MODEL?.trim() || 'deepseek-chat';
    const reasoningModel =
      process.env.AI_RUNTIME_REASONING_MODEL?.trim() || 'deepseek-reasoner';
    const codeModel = process.env.AI_RUNTIME_CODE_MODEL?.trim() || 'glm-5.2';

    switch (intent) {
      case 'code':
        return {
          intent,
          providerKey: 'glm',
          model: codeModel,
          tier: 'code',
        };
      case 'reasoning':
        return {
          intent,
          providerKey: 'deepseek',
          model: reasoningModel,
          tier: 'reasoning',
        };
      case 'summarize':
        return {
          intent,
          providerKey: 'glm',
          model: fastModel,
          tier: 'fast',
        };
      case 'social_draft':
        return {
          intent,
          providerKey: 'deepseek',
          model: fastModel,
          tier: 'fast',
        };
      case 'simple_qa':
        return {
          intent,
          providerKey: 'deepseek',
          model: fastModel,
          tier: 'fast',
        };
      default:
        return this.defaultRouteForSection(request.section, intent);
    }
  }

  private defaultRouteForSection(section: AiRuntimeSection, intent: AiRuntimeIntent): ModelRoute {
    const fastModel = process.env.AI_RUNTIME_FAST_MODEL?.trim() || 'deepseek-chat';
    if (section === 'wall_summarizer') {
      return { intent, providerKey: 'glm', model: fastModel, tier: 'fast' };
    }
    return { intent, providerKey: 'deepseek', model: fastModel, tier: 'fast' };
  }
}
