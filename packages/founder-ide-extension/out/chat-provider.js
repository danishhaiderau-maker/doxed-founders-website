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
exports.VENDOR = exports.FounderOsChatProvider = void 0;
/**
 * Founder OS `LanguageModelChatProvider` implementation.
 *
 * Uses the stable VS Code 1.104 (Aug 2025) API:
 *   - `provideLanguageModelChatInformation(options, token)` — list our model aliases
 *   - `provideLanguageModelChatResponse(model, messages, options, progress, token)` — stream
 *   - `provideTokenCount(model, text, token)` — approximate token estimate
 *
 * The provider bridges VS Code's Chat view to our OpenAI-compatible
 * `/api/v1/chat/completions` endpoint. The gateway owns routing / DDollar
 * spend / Flight Recorder logging — this extension is just the model layer.
 */
const vscode = __importStar(require("vscode"));
const credentials_1 = require("./credentials");
const models_1 = require("./models");
const gateway_client_1 = require("./gateway-client");
const memory_1 = require("./memory");
/** Convert our alias metadata into the VS Code `LanguageModelChatInformation` shape. */
function aliasToInfo(alias) {
    return {
        id: alias.id,
        name: alias.name,
        family: alias.family,
        version: '1',
        maxInputTokens: alias.maxInputTokens,
        maxOutputTokens: alias.maxOutputTokens,
        tooltip: alias.tooltip,
        detail: alias.detail,
        capabilities: {
            imageInput: false,
            toolCalling: false,
        },
    };
}
/** Pull plain text out of a `LanguageModelChatRequestMessage`'s part array. */
function messageContentToText(content) {
    const parts = [];
    for (const part of content) {
        if (part instanceof vscode.LanguageModelTextPart) {
            parts.push(part.value);
        }
        else if (part &&
            typeof part === 'object' &&
            'value' in part &&
            typeof part.value === 'string') {
            // Best-effort fallback for any other text-like part.
            parts.push(part.value);
        }
    }
    return parts.join('');
}
function roleToString(role) {
    // The VS Code enum only has User (1) and Assistant (2). System prompts are
    // not carried as a role in `LanguageModelChatRequestMessage`; if a future
    // version adds System, we map it to 'user' as a safe fallback for now and
    // rely on the gateway / Memory Engine to manage system context.
    if (role === vscode.LanguageModelChatMessageRole.Assistant)
        return 'assistant';
    return 'user';
}
class FounderOsChatProvider {
    events;
    requestTimeoutMs;
    founderOsMetadata;
    creds;
    constructor(creds, events = {}) {
        this.events = events;
        this.creds = creds;
        const cfg = vscode.workspace.getConfiguration('founderOs');
        this.requestTimeoutMs = cfg.get('requestTimeoutMs') ?? 120_000;
        // Metadata pre-line is opt-in. Server may not emit it yet; that's fine —
        // the parser just never sees a `founderOs` line and the cost stays hidden.
        this.founderOsMetadata = true;
        this.client = {
            baseUrl: (0, credentials_1.proxyBaseUrl)(creds.apiBaseUrl),
            bearer: (0, credentials_1.bearerFromCredentials)(creds),
        };
    }
    client;
    provideLanguageModelChatInformation(_options, _token) {
        return models_1.FOUNDER_OS_MODELS.map(aliasToInfo);
    }
    async provideLanguageModelChatResponse(model, messages, options, progress, token) {
        const alias = (0, models_1.findModelAlias)(model.id) ?? models_1.FOUNDER_OS_MODELS[0];
        this.events.onRequestStart?.(alias.id);
        // Convert VS Code messages → OpenAI-compatible messages. VS Code always
        // sends a System message first when the Chat participant defines one; we
        // pass it through unchanged so Memory Engine / system prompt injection
        // (Phase 4) can prepend to it.
        const gatewayMessages = messages.map((m) => ({
            role: roleToString(m.role),
            content: messageContentToText(m.content),
            name: m.name,
        }));
        // Memory Engine injection (design report §8.3). Fetch project + founder
        // memory for the current workspace and prepend it as a system message.
        // Best-effort: never breaks chat if the endpoint is missing or slow.
        try {
            const memory = await (0, memory_1.buildSystemPrompt)(this.creds, token);
            if (memory.hasMemory && memory.text.length > 0) {
                // If the first message is already a system message, merge so we don't
                // send two system blocks (some providers dislike that).
                if (gatewayMessages.length > 0 && gatewayMessages[0].role === 'system') {
                    gatewayMessages[0] = {
                        ...gatewayMessages[0],
                        content: `${memory.text}\n\n${gatewayMessages[0].content ?? ''}`,
                    };
                }
                else {
                    gatewayMessages.unshift({ role: 'system', content: memory.text });
                }
            }
        }
        catch {
            // Memory fetch must never block the chat.
        }
        let ok = false;
        let errorMessage;
        try {
            // `options.modelOptions` is an opaque `{ name: any }` map from the
            // user's language-model settings. We pass a temperature through only if
            // it's a recognizable number.
            const temperature = typeof options.modelOptions?.temperature === 'number'
                ? options.modelOptions.temperature
                : undefined;
            await (0, gateway_client_1.callGateway)(this.client, {
                model: alias.id,
                messages: gatewayMessages,
                executionProfile: alias.executionProfile,
                founderOsMetadata: this.founderOsMetadata,
                timeoutMs: this.requestTimeoutMs,
                temperature,
            }, {
                onToken: (delta) => {
                    if (token.isCancellationRequested)
                        return;
                    progress.report(new vscode.LanguageModelTextPart(delta));
                },
                onMetadata: (meta) => this.events.onMetadata?.(meta),
                onError: (_status, body) => {
                    errorMessage = body.slice(0, 300);
                },
            }, token);
            ok = !token.isCancellationRequested;
        }
        catch (err) {
            errorMessage = err instanceof Error ? err.message : String(err);
            if (!token.isCancellationRequested) {
                // Surface the error in-line in the chat so the user sees why it failed.
                progress.report(new vscode.LanguageModelTextPart(`\n\n_Founder OS gateway error: ${errorMessage}_`));
            }
        }
        finally {
            this.events.onRequestEnd?.(alias.id, ok, errorMessage);
        }
    }
    provideTokenCount(_model, text, _token) {
        const str = typeof text === 'string'
            ? text
            : messageContentToText(text.content);
        // Rough OpenAI-style estimate (~4 chars/token). Good enough for context
        // window management; the gateway does the real accounting server-side.
        return Promise.resolve(Math.max(1, Math.ceil(str.length / 4)));
    }
}
exports.FounderOsChatProvider = FounderOsChatProvider;
/** Vendor ID used in `package.json` `contributes.languageModelChatProviders`. */
exports.VENDOR = models_1.FOUNDER_OS_VENDOR;
//# sourceMappingURL=chat-provider.js.map