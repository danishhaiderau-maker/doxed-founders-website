"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.findModelAlias = void 0;
exports.registerFounderOsChatParticipant = registerFounderOsChatParticipant;
/**
 * `@Founder OS` chat participant — VS Code 1.93+ stable ChatParticipant API.
 *
 * It streams responses directly from the Founder OS gateway into the Chat view
 * and uses VS Code's native tool confirmation flow for workspace reads,
 * reviewed edits, and visible commands.
 */
const vscode = __importStar(require("vscode"));
const models_1 = require("./models");
Object.defineProperty(exports, "findModelAlias", { enumerable: true, get: function () { return models_1.findModelAlias; } });
const memory_1 = require("./memory");
const credentials_1 = require("./credentials");
const gateway_client_1 = require("./gateway-client");
const tool_names_1 = require("./tool-names");
const MAX_TOOL_TURNS = 8;
function availableFounderTools() {
    const tools = vscode.lm.tools;
    return (tools ?? []).filter((tool) => tool_names_1.FOUNDER_TOOL_NAMES.has(tool.name));
}
function toolResultText(result) {
    const parts = [];
    for (const part of result.content) {
        if (part &&
            typeof part === 'object' &&
            'value' in part &&
            typeof part.value === 'string') {
            parts.push(part.value);
        }
        else {
            try {
                parts.push(JSON.stringify(part));
            }
            catch {
                parts.push(String(part));
            }
        }
    }
    return parts.join('\n').slice(0, 40_000);
}
function parseToolInput(argumentsJson) {
    try {
        const parsed = JSON.parse(argumentsJson);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            return parsed;
        }
    }
    catch {
        // The tool receives a visible error result below.
    }
    return { rawArguments: argumentsJson };
}
function registerFounderOsChatParticipant(context, deps) {
    let participant;
    try {
        participant = vscode.chat.createChatParticipant('founder-os.chat', (request, chatContext, stream, token) => handleParticipantRequest(request, chatContext, stream, deps, token));
    }
    catch {
        // Another extension may have claimed the id. Non-fatal.
        return undefined;
    }
    participant.iconPath = new vscode.ThemeIcon('sparkle');
    context.subscriptions.push(participant);
    return participant;
}
async function handleParticipantRequest(request, _context, stream, deps, token) {
    const prompt = request.prompt ?? '';
    if (prompt.trim().length === 0) {
        stream.markdown('Type a message and I’ll route it through your Founder OS gateway.');
        return;
    }
    // Resolve the alias for the active execution profile (or `/code` slash cmd).
    const desiredAlias = deps.profileManager.alias;
    let alias = desiredAlias ?? models_1.FOUNDER_OS_MODELS[0];
    if (request.command === 'code') {
        const codeModel = (0, models_1.findModelAlias)('founder-os-code');
        if (codeModel)
            alias = codeModel;
    }
    deps.onRequestStart?.(alias.id);
    const coordinationTaskId = deps.coordination?.begin(prompt, alias.id);
    // Build the system prompt with Memory Engine context.
    let memoryText = '';
    try {
        const memory = await (0, memory_1.buildSystemPrompt)(deps.creds, token);
        if (memory.hasMemory && memory.text.length > 0)
            memoryText = memory.text;
    }
    catch {
        /* memory must never block chat */
    }
    let coordinationText = coordinationTaskId
        ? deps.coordination?.contextFor(coordinationTaskId) ?? ''
        : '';
    const identity = 'You are Founder OS, the founder\'s AI pair-programmer routed via their own gateway. Inspect the workspace before changing it. Use the available tools to make requested code changes and verify them; do not merely describe work that can be completed locally. Be concise and direct.';
    const systemContent = [memoryText, coordinationText, identity].filter(Boolean).join('\n\n');
    const gatewayMessages = [
        { role: 'system', content: systemContent },
        { role: 'user', content: prompt },
    ];
    const client = {
        baseUrl: (0, credentials_1.proxyBaseUrl)(deps.creds.apiBaseUrl),
        bearer: (0, credentials_1.authorizationHeaderFromCredentials)(deps.creds),
    };
    const cfg = vscode.workspace.getConfiguration('founderOs');
    const timeoutMs = cfg.get('requestTimeoutMs') ?? 120_000;
    let ok = false;
    let errorMessage;
    try {
        const tools = availableFounderTools();
        let completed = false;
        for (let turn = 0; turn < MAX_TOOL_TURNS && !token.isCancellationRequested; turn += 1) {
            if (turn > 0 && coordinationTaskId) {
                const refreshed = deps.coordination?.contextFor(coordinationTaskId) ?? '';
                if (refreshed && refreshed !== coordinationText) {
                    coordinationText = refreshed;
                    gatewayMessages.push({ role: 'system', content: refreshed });
                    stream.progress('Founder Agents: coordination refreshed');
                }
            }
            const toolCalls = [];
            let assistantText = '';
            await (0, gateway_client_1.callGateway)(client, {
                model: alias.id,
                messages: gatewayMessages,
                executionProfile: alias.executionProfile,
                founderOsMetadata: true,
                timeoutMs,
                tools: tools.map((tool) => ({
                    type: 'function',
                    function: {
                        name: tool.name,
                        description: tool.description,
                        parameters: tool.inputSchema,
                    },
                })),
                toolChoice: 'auto',
            }, {
                onToken: (delta) => {
                    if (token.isCancellationRequested)
                        return;
                    assistantText += delta;
                    stream.markdown(delta);
                },
                onToolCall: (call) => toolCalls.push(call),
                onMetadata: (meta) => {
                    deps.onMetadata?.(meta);
                },
                onError: (status, body) => {
                    errorMessage = (0, gateway_client_1.gatewayUserMessage)(status, body);
                },
            }, token);
            if (toolCalls.length === 0) {
                completed = true;
                break;
            }
            gatewayMessages.push({
                role: 'assistant',
                content: assistantText,
                tool_calls: toolCalls.map((call) => ({
                    id: call.id,
                    type: 'function',
                    function: { name: call.name, arguments: call.arguments },
                })),
            });
            for (const call of toolCalls) {
                let resultText;
                if (!tool_names_1.FOUNDER_TOOL_NAMES.has(call.name)) {
                    resultText = `Error: tool "${call.name}" is not available.`;
                }
                else {
                    stream.progress(`Founder OS: ${call.name}`);
                    try {
                        const result = await vscode.lm.invokeTool(call.name, {
                            input: parseToolInput(call.arguments),
                            toolInvocationToken: request.toolInvocationToken,
                        }, token);
                        resultText = toolResultText(result);
                    }
                    catch (error) {
                        resultText = `Error: ${error instanceof Error ? error.message : String(error)}`;
                    }
                }
                gatewayMessages.push({
                    role: 'tool',
                    name: call.name,
                    tool_call_id: call.id,
                    content: resultText,
                });
            }
        }
        if (!completed && !token.isCancellationRequested) {
            stream.markdown('\n\n_Founder OS stopped after the tool-turn safety limit._');
        }
        ok = completed && !token.isCancellationRequested;
    }
    catch (err) {
        errorMessage = err instanceof Error ? err.message : String(err);
        if (!token.isCancellationRequested) {
            stream.markdown(`\n\n_${errorMessage}_`);
        }
        return;
    }
    finally {
        if (coordinationTaskId)
            deps.coordination?.end(coordinationTaskId);
        deps.onRequestEnd?.(alias.id, ok, errorMessage);
    }
    if (!ok && errorMessage) {
        stream.markdown(`\n\n_Founder OS request failed: ${errorMessage}_`);
    }
}
//# sourceMappingURL=chat-participant.js.map