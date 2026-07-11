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
 * Enhanced `@Founder OS` chat participant.
 *
 * Phase 1's participant was onboarding-only (just told the user to pick a model).
 * Phase 4 makes it actually drive a `vscode.lm` round-trip: when the user types
 * `@Founder OS <message>`, we select the founder-os model for the active
 * execution profile, build a Memory-Engine-injected system prompt, and stream
 * the response into the chat via the ChatResponseStream.
 *
 * Tool use: the participant passes the registered `founder.*` tools to the
 * model. When the model emits a `LanguageModelToolCallPart`, we invoke the tool
 * via `vscode.lm.invokeTool`, append the result, and re-request — implementing
 * the agent loop described in design report §8.4.
 */
const vscode = __importStar(require("vscode"));
const models_1 = require("./models");
Object.defineProperty(exports, "findModelAlias", { enumerable: true, get: function () { return models_1.findModelAlias; } });
const memory_1 = require("./memory");
/** Tool names registered by the extension that we expose to the model. */
const FOUNDER_TOOL_NAMES = [
    'founder.editFile',
    'founder.runCommand',
    'founder.readWorkspace',
];
const MAX_TOOL_ROUNDS = 6;
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
    const profile = deps.profileManager.profile;
    const desiredAlias = deps.profileManager.alias;
    // Resolve a founder-os LanguageModelChat. Prefer the profile's alias; fall
    // back to any founder-os model if the alias isn't selectable (e.g. older API).
    const models = await vscode.lm.selectChatModels({ vendor: models_1.FOUNDER_OS_VENDOR });
    if (!models || models.length === 0) {
        stream.markdown('Founder OS is not connected. Pair Founder Node (or set `founderOs.*` settings) and reload, then try again.');
        return;
    }
    let model = models.find((m) => m.id === desiredAlias.id) ?? models[0];
    // Honour the `/code` slash command if present — force the coding alias.
    if (request.command === 'code') {
        const codeModel = models.find((m) => m.id === 'founder-os-code');
        if (codeModel)
            model = codeModel;
    }
    // Build the system prompt with Memory Engine context.
    const memory = await (0, memory_1.buildSystemPrompt)(deps.creds, token);
    const systemMessage = vscode.LanguageModelChatMessage.User(memory.hasMemory && memory.text.length > 0
        ? `${memory.text}\n\nYou are Founder OS, the founder's AI pair-programmer. Be concise and direct.`
        : 'You are Founder OS, the founder\'s AI pair-programmer routed via their own gateway. Be concise and direct.');
    const userMessage = vscode.LanguageModelChatMessage.User(request.prompt);
    // Gather tool references for the registered founder.* tools.
    const availableTools = vscode.lm.tools.filter((t) => FOUNDER_TOOL_NAMES.includes(t.name));
    const toolRefs = availableTools.map((info) => ({
        name: info.name,
        description: info.description,
        inputSchema: info.inputSchema,
    }));
    const requestOptions = {
        justification: 'Answering a Founder OS chat request via the founder\'s own gateway.',
        tools: toolRefs,
        toolMode: vscode.LanguageModelChatToolMode.Auto,
    };
    const messages = [systemMessage, userMessage];
    let rounds = 0;
    while (rounds < MAX_TOOL_ROUNDS) {
        rounds++;
        let response;
        try {
            response = await model.sendRequest(messages, requestOptions, token);
        }
        catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            stream.markdown(`\n\n_Founder OS request failed: ${msg}_`);
            return;
        }
        // Consume the stream, forwarding text to the chat and collecting tool calls.
        const toolCalls = [];
        try {
            for await (const chunk of response.stream) {
                if (chunk instanceof vscode.LanguageModelTextPart) {
                    stream.markdown(chunk.value);
                }
                else if (chunk instanceof vscode.LanguageModelToolCallPart) {
                    toolCalls.push(chunk);
                }
            }
        }
        catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            stream.markdown(`\n\n_Founder OS stream error: ${msg}_`);
            return;
        }
        if (toolCalls.length === 0) {
            return; // pure text response — done.
        }
        // Record the assistant's tool calls, then invoke each tool and append the
        // results as a user message (per the VS Code Chat API contract).
        const assistantTurn = vscode.LanguageModelChatMessage.Assistant(toolCalls);
        messages.push(assistantTurn);
        const resultParts = [];
        for (const call of toolCalls) {
            try {
                const result = await vscode.lm.invokeTool(call.name, {
                    toolInvocationToken: request.toolInvocationToken,
                    input: call.input,
                }, token);
                // Surface a short note in the chat so the founder sees what happened.
                const firstText = result.content
                    .find((c) => c instanceof vscode.LanguageModelTextPart);
                stream.markdown(`\n\n_Tool \`${call.name}\` ran: ${(firstText?.value ?? '(ok)').slice(0, 200)}_\n\n`);
                resultParts.push(new vscode.LanguageModelToolResultPart(call.callId, result.content));
            }
            catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                stream.markdown(`\n\n_Tool \`${call.name}\` failed: ${msg}_\n\n`);
                resultParts.push(new vscode.LanguageModelToolResultPart(call.callId, [
                    new vscode.LanguageModelTextPart(`Tool error: ${msg}`),
                ]));
            }
        }
        messages.push(vscode.LanguageModelChatMessage.User(resultParts));
        // Loop again so the model can react to the tool results.
    }
    stream.markdown(`\n\n_Founder OS: reached the ${MAX_TOOL_ROUNDS}-round tool-use limit for this turn._`);
}
//# sourceMappingURL=chat-participant.js.map