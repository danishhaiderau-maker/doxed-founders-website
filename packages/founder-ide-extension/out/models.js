"use strict";
/**
 * Model alias definitions for the Founder OS chat provider.
 *
 * These mirror `apps/api/src/ai-proxy/ai-proxy.constants.ts` and
 * `packages/utils/src/ai-proxy.ts` so the IDE's model picker maps 1:1 to the
 * gateway's routing tiers. The gateway's `decideRoute` ultimately picks the
 * concrete provider+model; the alias here is just the user-facing handle.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEFAULT_FOUNDER_OS_MODEL_ID = exports.FOUNDER_OS_MODELS = exports.FOUNDER_OS_VENDOR = void 0;
exports.findModelAlias = findModelAlias;
exports.FOUNDER_OS_VENDOR = 'founder-os';
exports.FOUNDER_OS_MODELS = [
    {
        id: 'founder-os-auto',
        name: 'Founder OS Auto',
        family: 'founder-os',
        tooltip: 'Default — DeepSeek V4 Flash with a truthful route receipt.',
        detail: 'Cost-efficient default for everyday work. Use Code or Reasoning when you explicitly want V4 Pro.',
        executionProfile: 'auto',
        isDefault: true,
        maxInputTokens: 64_000,
        maxOutputTokens: 8_192,
    },
    {
        id: 'founder-os-code',
        name: 'Founder OS Code',
        family: 'founder-os',
        tooltip: 'Code — DeepSeek V4 Pro for complex implementation.',
        detail: 'Complex coding profile. Uses the managed V4 Pro route or your selected personal model.',
        executionProfile: 'turbo',
        isDefault: false,
        maxInputTokens: 64_000,
        maxOutputTokens: 8_192,
    },
    {
        id: 'founder-os-reasoning',
        name: 'Founder OS Reasoning',
        family: 'founder-os',
        tooltip: 'Reasoning — DeepSeek V4 Pro for deliberate analysis.',
        detail: 'Deep reasoning profile with the provider, model, tokens, and latency shown in the route receipt.',
        executionProfile: 'architect',
        isDefault: false,
        maxInputTokens: 64_000,
        maxOutputTokens: 8_192,
    },
    {
        id: 'founder-os-fast',
        name: 'Founder OS Fast',
        family: 'founder-os',
        tooltip: 'Fast — DeepSeek V4 Flash non-thinking for the lowest latency.',
        detail: 'Quick questions, summaries, and small edits on the managed V4 Flash route.',
        executionProfile: 'turbo',
        isDefault: false,
        maxInputTokens: 32_000,
        maxOutputTokens: 4_096,
    },
];
exports.DEFAULT_FOUNDER_OS_MODEL_ID = 'founder-os-auto';
function findModelAlias(id) {
    return exports.FOUNDER_OS_MODELS.find((m) => m.id === id);
}
//# sourceMappingURL=models.js.map