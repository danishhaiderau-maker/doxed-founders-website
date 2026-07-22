"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DEFAULT_TOOL_RESULT_CHAR_BUDGET = void 0;
exports.estimateTokensFromText = estimateTokensFromText;
exports.estimateMessagesTokens = estimateMessagesTokens;
exports.composeFounderSystemPrompt = composeFounderSystemPrompt;
exports.planPromptEfficiency = planPromptEfficiency;
const node_crypto_1 = require("node:crypto");
exports.DEFAULT_TOOL_RESULT_CHAR_BUDGET = 16_000;
const COORDINATION_PREFIX = '## Live agent coordination';
function estimateTokensFromText(value) {
    return value.length === 0 ? 0 : Math.ceil(value.length / 4);
}
function estimateMessagesTokens(messages) {
    return messages.reduce((total, message) => {
        const toolCallChars = message.tool_calls ? JSON.stringify(message.tool_calls) : '';
        return total + estimateTokensFromText(message.content) + estimateTokensFromText(toolCallChars) + 4;
    }, 0);
}
function composeFounderSystemPrompt(input) {
    return [input.identity, input.memory, input.projectContext, input.coordination]
        .map((block) => block?.trim() ?? '')
        .filter(Boolean)
        .join('\n\n');
}
function compactToolResult(content, budget) {
    if (content.length <= budget)
        return content;
    const digest = (0, node_crypto_1.createHash)('sha256').update(content, 'utf8').digest('hex').slice(0, 16);
    const marker = `\n\n[Founder compacted ${content.length - budget} characters; full output remains in the local terminal; sha256:${digest}]\n\n`;
    const available = Math.max(200, budget - marker.length);
    const head = Math.floor(available * 0.6);
    return `${content.slice(0, head)}${marker}${content.slice(-(available - head))}`;
}
function planPromptEfficiency(input, options = {}) {
    const baselineTokens = estimateMessagesTokens(input);
    const maxToolResultChars = Math.max(1_000, options.maxToolResultChars ?? exports.DEFAULT_TOOL_RESULT_CHAR_BUDGET);
    const latestCoordination = input.reduce((latest, message, index) => message.role === 'system' && message.content.trimStart().startsWith(COORDINATION_PREFIX)
        ? index
        : latest, -1);
    let compactedToolResults = 0;
    let removedStaleCoordinationBlocks = 0;
    const messages = [];
    for (let index = 0; index < input.length; index += 1) {
        const message = input[index];
        if (message.role === 'system'
            && message.content.trimStart().startsWith(COORDINATION_PREFIX)
            && index !== latestCoordination) {
            removedStaleCoordinationBlocks += 1;
            continue;
        }
        if (message.role === 'tool' && message.content.length > maxToolResultChars) {
            compactedToolResults += 1;
            messages.push({ ...message, content: compactToolResult(message.content, maxToolResultChars) });
            continue;
        }
        messages.push({ ...message });
    }
    const sentTokens = estimateMessagesTokens(messages);
    const avoidedTokens = Math.max(0, baselineTokens - sentTokens);
    const techniques = ['stable-system-prefix'];
    if (compactedToolResults > 0)
        techniques.push('bounded-tool-results');
    if (removedStaleCoordinationBlocks > 0)
        techniques.push('latest-coordination-only');
    return {
        messages,
        estimate: {
            measurement: 'estimated',
            baselineTokens,
            sentTokens,
            avoidedTokens,
            savingsPercent: baselineTokens > 0
                ? Math.round((avoidedTokens / baselineTokens) * 10_000) / 100
                : 0,
            compactedToolResults,
            removedStaleCoordinationBlocks,
            techniques,
        },
    };
}
//# sourceMappingURL=prompt-efficiency.js.map