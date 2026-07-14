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
 * On VS Code 1.104+ this extension also registers a `LanguageModelChatProvider`
 * so Founder OS models appear in the built-in model picker. That provider API
 * does not exist in VS Code 1.93.1, so on older builds the participant is the
 * primary surface: it streams responses straight from the Founder OS gateway
 * into the Chat view via `stream.markdown()`, using the OpenAI-compatible SSE
 * client in `gateway-client.ts`.
 *
 * The handler is intentionally tolerant: if `vscode.lm.selectChatModels` returns
 * Founder OS models (newer VS Code), it forwards through the model API; otherwise
 * it falls back to a direct gateway call. Either way the user sees streamed text
 * in the Chat box.
 */
const vscode = __importStar(require("vscode"));
const models_1 = require("./models");
Object.defineProperty(exports, "findModelAlias", { enumerable: true, get: function () { return models_1.findModelAlias; } });
const memory_1 = require("./memory");
const credentials_1 = require("./credentials");
const gateway_client_1 = require("./gateway-client");
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
    const systemContent = memoryText.length > 0
        ? `${memoryText}\n\nYou are Founder OS, the founder's AI pair-programmer. Be concise and direct.`
        : 'You are Founder OS, the founder\'s AI pair-programmer routed via their own gateway. Be concise and direct.';
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
        await (0, gateway_client_1.callGateway)(client, {
            model: alias.id,
            messages: gatewayMessages,
            executionProfile: alias.executionProfile,
            founderOsMetadata: true,
            timeoutMs,
        }, {
            onToken: (delta) => {
                if (token.isCancellationRequested)
                    return;
                stream.markdown(delta);
            },
            onMetadata: (meta) => {
                deps.costTracker?.record(meta);
            },
            onError: (_status, body) => {
                errorMessage = body.slice(0, 300);
            },
        }, token);
        ok = !token.isCancellationRequested;
    }
    catch (err) {
        errorMessage = err instanceof Error ? err.message : String(err);
        if (!token.isCancellationRequested) {
            stream.markdown(`\n\n_Founder OS gateway error: ${errorMessage}_`);
        }
        return;
    }
    if (!ok && errorMessage) {
        stream.markdown(`\n\n_Founder OS request failed: ${errorMessage}_`);
    }
}
//# sourceMappingURL=chat-participant.js.map