/**
 * Capability Registry seed values.
 *
 * IMPORTANT: All cost, latency and intent-score values below are PROPOSAL
 * values, not measurements. They exist so the Routing Engine has something
 * to score against on day one. The Learning Engine (Phase 4 — see
 * docs/KERNEL.md §10) will refine `successRate` / `retryRate` from real
 * traffic, and a founder review pass can adjust the intent scores based
 * on observed quality.
 *
 * The shape of each row MUST match `CapabilitySeed` in
 * ./capability-registry.types.ts AND the field names read by
 * scripts/seed-capabilities.ts. The seed script reads by name (seed.inputCostPer1M,
 * seed.largeContextWindow, etc.), so renames here will break seeding.
 */
import type { CapabilitySeed } from './capability-registry.types';
import {
  DEEPSEEK_V4_FLASH_MODEL,
  DEEPSEEK_V4_PRO_MODEL,
} from '../ai-proxy/deepseek-model-policy';

export const CAPABILITY_SEEDS: CapabilitySeed[] = [
  {
    provider: 'glm',
    model: 'glm-5.2',
    displayName: 'GLM 5.2',
    // Disabled until its platform credential passes a real completion smoke.
    isActive: false,
    inputCostPer1M: 0.5,
    outputCostPer1M: 1.5,
    latencyP50Ms: 1500,
    codeScore: 0.95,
    reasoningScore: 0.85,
    simpleQaScore: 0.8,
    agentScore: 0.75,
    visionScore: 0.0,
    toolUse: true,
    jsonMode: true,
    largeContext: false,
    largeContextWindow: null,
  },
  {
    provider: 'glm',
    model: 'glm-4.6',
    displayName: 'GLM 4.6',
    isActive: false,
    inputCostPer1M: 0.2,
    outputCostPer1M: 0.6,
    latencyP50Ms: 1200,
    codeScore: 0.8,
    reasoningScore: 0.65,
    simpleQaScore: 0.75,
    agentScore: 0.55,
    visionScore: 0.0,
    toolUse: false,
    jsonMode: true,
    largeContext: true,
    largeContextWindow: 200000,
  },
  {
    provider: 'deepseek',
    model: DEEPSEEK_V4_PRO_MODEL,
    displayName: 'DeepSeek V4 Pro',
    inputCostPer1M: 0.435,
    outputCostPer1M: 0.87,
    latencyP50Ms: 1800,
    codeScore: 0.92,
    reasoningScore: 0.95,
    simpleQaScore: 0.72,
    agentScore: 0.6,
    visionScore: 0.0,
    toolUse: false,
    jsonMode: true,
    largeContext: false,
    largeContextWindow: null,
  },
  {
    provider: 'deepseek',
    model: DEEPSEEK_V4_FLASH_MODEL,
    displayName: 'DeepSeek V4 Flash',
    inputCostPer1M: 0.14,
    outputCostPer1M: 0.28,
    latencyP50Ms: 900,
    codeScore: 0.78,
    reasoningScore: 0.6,
    simpleQaScore: 0.82,
    agentScore: 0.45,
    visionScore: 0.0,
    toolUse: false,
    jsonMode: true,
    largeContext: false,
    largeContextWindow: null,
  },
  {
    provider: 'kimi',
    model: 'kimi-coder',
    displayName: 'Kimi Coder',
    // Catalogued for research/history only. The Founder gateway does not yet
    // have a Kimi upstream adapter, so it must not enter chat routing.
    isActive: false,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.45,
    latencyP50Ms: 900,
    codeScore: 0.78,
    reasoningScore: 0.55,
    simpleQaScore: 0.65,
    agentScore: 0.45,
    visionScore: 0.0,
    toolUse: false,
    jsonMode: true,
    largeContext: false,
    largeContextWindow: null,
  },
  {
    provider: 'kimi',
    model: 'kimi-k2',
    displayName: 'Kimi K2',
    isActive: false,
    inputCostPer1M: 0.6,
    outputCostPer1M: 2.5,
    latencyP50Ms: 1800,
    codeScore: 0.85,
    reasoningScore: 0.92,
    simpleQaScore: 0.8,
    agentScore: 0.82,
    visionScore: 0.0,
    toolUse: true,
    jsonMode: true,
    largeContext: true,
    largeContextWindow: 200000,
  },
  {
    // Phase 6 — the first non-LLM capability in the registry. Backs the
    // Browser Use research hand (browser-research.adapter.ts). Not routed
    // to by the intent+cost scoring (it's an Execution Engine adapter,
    // not a chat model); it's here so the Flight Recorder rows it emits
    // (chosenProvider: 'local-playwright') join cleanly to a Capability,
    // and so per-check cost can be attributed. See KERNEL.md §8.
    provider: 'local-playwright',
    model: 'chromium-headless',
    displayName: 'Local Playwright (Chromium headless)',
    isActive: false,
    inputCostPer1M: 0,
    outputCostPer1M: 0,
    latencyP50Ms: 4000,
    codeScore: 0.0,
    reasoningScore: 0.0,
    simpleQaScore: 0.0,
    agentScore: 0.9,
    visionScore: 0.0,
    toolUse: false,
    jsonMode: false,
    largeContext: false,
    largeContextWindow: null,
  },
];
