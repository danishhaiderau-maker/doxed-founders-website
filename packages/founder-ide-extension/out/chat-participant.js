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
const prompt_efficiency_1 = require("./prompt-efficiency");
const agent_task_context_1 = require("./agent-task-context");
const founder_agent_mode_1 = require("./founder-agent-mode");
const auto_escalation_1 = require("./auto-escalation");
const verified_solution_memory_1 = require("./verified-solution-memory");
const personal_ai_profiles_1 = require("./personal-ai-profiles");
const completion_evidence_1 = require("./completion-evidence");
const founder_agent_mode_2 = require("./founder-agent-mode");
const founder_goal_context_1 = require("./founder-goal-context");
const MAX_TOOL_TURNS = 8;
function availableFounderTools(mode) {
    const tools = vscode.lm.tools;
    const allowedNames = new Set((0, completion_evidence_1.founderToolsForMode)(mode, [...(tools ?? [])].map((tool) => tool.name)));
    return [...(tools ?? [])]
        .filter((tool) => tool_names_1.FOUNDER_TOOL_NAMES.has(tool.name))
        .filter((tool) => allowedNames.has(tool.name))
        .sort((left, right) => left.name.localeCompare(right.name));
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
    await deps.personalAiProfiles?.ready();
    const personalProfile = deps.personalAiProfiles?.active() ?? null;
    // Resolve the managed alias for the active route (or `/code` slash cmd).
    const desiredAlias = deps.profileManager.alias;
    let alias = desiredAlias ?? models_1.FOUNDER_OS_MODELS[0];
    if (request.command === 'code') {
        const codeModel = (0, models_1.findModelAlias)('founder-os-code');
        if (codeModel)
            alias = codeModel;
    }
    const autoSelected = !personalProfile && alias.id === 'founder-os-auto';
    let escalationReason = autoSelected
        ? (0, auto_escalation_1.founderAutoEscalationReason)([{ role: 'user', content: prompt }])
        : null;
    if (escalationReason) {
        alias = (0, models_1.findModelAlias)('founder-os-reasoning') ?? alias;
    }
    const selectedModelId = personalProfile?.model ?? alias.id;
    deps.onRequestStart?.(selectedModelId);
    const agentMode = (0, founder_agent_mode_1.normalizeFounderAgentMode)(vscode.workspace.getConfiguration('founderOs').get('agentMode'));
    const workMode = (0, founder_agent_mode_2.readFounderWorkMode)();
    const coordinationTaskId = deps.coordination?.begin(prompt, personalProfile ? `personal:${personalProfile.name}/${personalProfile.model}` : alias.id, agentMode);
    // Build the system prompt with Memory Engine context.
    let memoryText = '';
    try {
        if (deps.creds) {
            const memory = await (0, memory_1.buildSystemPrompt)(deps.creds, token);
            if (memory.hasMemory && memory.text.length > 0)
                memoryText = memory.text;
        }
    }
    catch {
        /* memory must never block chat */
    }
    let coordinationText = coordinationTaskId
        ? deps.coordination?.contextFor(coordinationTaskId) ?? ''
        : '';
    const projectContextText = deps.projectContext?.contextFor(prompt) ?? '';
    const cacheContext = deps.projectContext?.cacheContextFor(prompt) ?? null;
    const workspaceId = deps.projectContext?.workspaceIdValue()
        ?? cacheContext?.workspaceId
        ?? null;
    const activityId = workspaceId
        ? deps.projectActivity?.begin(workspaceId, prompt, selectedModelId) ?? null
        : null;
    const priorSolutions = workspaceId
        ? deps.solutionMemory?.contextFor(workspaceId, prompt, deps.projectContext?.allFileHashes() ?? []) ?? ''
        : '';
    const goalContext = (0, founder_goal_context_1.renderFounderGoalContext)(deps.goalControl?.snapshot() ?? null);
    const identity = [
        'You are Founder OS, the founder\'s AI pair-programmer. Inspect the workspace before changing it. Use the available tools to make requested code changes and verify them; do not merely describe work that can be completed locally. Be concise and direct.',
        (0, completion_evidence_1.founderWorkModeInstruction)(workMode),
        'Do not claim completion based on prose. Founder IDE evaluates locally observed edits and checks and appends the authoritative verification receipt.',
    ].join(' ');
    const systemMessages = (0, prompt_efficiency_1.composeFounderPromptMessages)({
        identity,
        memory: memoryText,
        projectContext: projectContextText,
        additionalStableContext: [goalContext, priorSolutions]
            .filter(Boolean)
            .join('\n\n'),
        coordination: coordinationText,
    });
    const gatewayMessages = [
        ...systemMessages,
        { role: 'user', content: prompt },
    ];
    const cacheInput = cacheContext
        ? { prompt, model: alias.id, context: cacheContext }
        : null;
    const cached = cacheInput ? deps.resultCache?.get(cacheInput) : null;
    if (cached) {
        stream.markdown(cached.text);
        stream.markdown(`\n\n---\n**Founder reuse** | safe read-only result | no provider request | ~${cached.estimatedTokensAvoided.toLocaleString()} tokens avoided (estimated)`);
        if (coordinationTaskId)
            deps.coordination?.end(coordinationTaskId);
        deps.onRequestEnd?.(selectedModelId, true);
        deps.onCacheHit?.(cached.estimatedTokensAvoided);
        deps.projectActivity?.complete(activityId, {
            status: 'reused',
            summary: 'Reused a matching read-only result after relevant context hashes were verified.',
            estimatedTokensAvoided: cached.estimatedTokensAvoided,
            verification: (0, completion_evidence_1.evaluateFounderCompletionEvidence)({
                mode: workMode,
                goal: prompt,
                finalAnswer: cached.text,
                requestCompleted: true,
                editedFiles: [],
                passedChecks: [],
            }),
        });
        return;
    }
    if (!personalProfile && !deps.creds) {
        stream.markdown('Sign in to use Founder-managed AI, or select a Personal AI or Ollama profile in Founder Settings.');
        if (coordinationTaskId)
            deps.coordination?.end(coordinationTaskId);
        deps.onRequestEnd?.(selectedModelId, false, 'Sign in or select Personal AI.');
        deps.projectActivity?.complete(activityId, {
            status: 'failed',
            summary: 'Managed AI requires Founder sign-in; no personal or local profile was selected.',
        });
        return;
    }
    const client = personalProfile
        ? {
            baseUrl: (0, personal_ai_profiles_1.personalAiApiBase)(personalProfile),
            bearer: personalProfile.apiKey,
            headers: (0, personal_ai_profiles_1.personalAiRequestHeaders)({ ...personalProfile, apiKey: '' }),
        }
        : {
            baseUrl: (0, credentials_1.proxyBaseUrl)(deps.creds.apiBaseUrl),
            bearer: (0, credentials_1.authorizationHeaderFromCredentials)(deps.creds),
        };
    const cfg = vscode.workspace.getConfiguration('founderOs');
    const timeoutMs = cfg.get('requestTimeoutMs') ?? 120_000;
    let ok = false;
    let errorMessage;
    let activitySummary = '';
    let activityProvider = null;
    let activityProviderModel = null;
    let activityEditedFiles = [];
    let activityChecks = [];
    let activityVerification = null;
    let providerCompleted = false;
    const providerUsageEvidence = {};
    let inputCostComparison;
    const requestStartedAt = Date.now();
    if (personalProfile) {
        activityProvider = personalProfile.kind === 'ollama' ? 'ollama' : personalProfile.name;
        activityProviderModel = personalProfile.model;
        deps.onMetadata?.({
            tier: personalProfile.kind === 'ollama' ? 'local' : 'personal',
            provider: activityProvider,
            model: personalProfile.model,
        });
    }
    try {
        const tools = availableFounderTools(workMode);
        let completed = false;
        let usedTools = false;
        let reusableAnswer = '';
        let reusableTokenEstimate = 0;
        const editedPaths = new Set();
        const passedChecks = [];
        let finalAnswer = '';
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
            const efficiency = (0, prompt_efficiency_1.planPromptEfficiency)(gatewayMessages);
            await (0, gateway_client_1.callGateway)(client, {
                model: personalProfile?.model ?? alias.id,
                messages: efficiency.messages,
                executionProfile: personalProfile ? undefined : alias.executionProfile,
                founderOsMetadata: !personalProfile,
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
                metadata: personalProfile
                    ? undefined
                    : {
                        founder_memory_included: memoryText.length > 0,
                        prompt_efficiency: efficiency.estimate,
                        ...(escalationReason ? { founder_auto_escalation: escalationReason } : {}),
                    },
            }, {
                onToken: (delta) => {
                    if (token.isCancellationRequested)
                        return;
                    assistantText += delta;
                    stream.markdown(delta);
                },
                onToolCall: (call) => toolCalls.push(call),
                onMetadata: (meta) => {
                    activityProvider = typeof meta.provider === 'string' ? meta.provider : activityProvider;
                    activityProviderModel = typeof meta.model === 'string' ? meta.model : activityProviderModel;
                    inputCostComparison = meta.inputCostComparison ?? inputCostComparison;
                    deps.onMetadata?.(meta);
                },
                onUsage: (usage) => {
                    providerUsageEvidence.value = usage;
                },
                onError: (status, body) => {
                    errorMessage = personalProfile
                        ? personalProviderUserMessage(personalProfile, status)
                        : (0, gateway_client_1.gatewayUserMessage)(status, body);
                },
            }, token);
            if (toolCalls.length === 0) {
                completed = true;
                reusableAnswer = assistantText;
                reusableTokenEstimate = efficiency.estimate.sentTokens + (0, prompt_efficiency_1.estimateTokensFromText)(assistantText);
                finalAnswer = assistantText;
                break;
            }
            usedTools = true;
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
                const parsedInput = parseToolInput(call.arguments);
                if (!tool_names_1.FOUNDER_TOOL_NAMES.has(call.name)) {
                    resultText = `Error: tool "${call.name}" is not available.`;
                }
                else {
                    stream.progress(`Founder OS: ${call.name}`);
                    try {
                        const invoke = () => vscode.lm.invokeTool(call.name, {
                            input: parsedInput,
                            toolInvocationToken: request.toolInvocationToken,
                        }, token);
                        const result = coordinationTaskId
                            ? await (0, agent_task_context_1.runWithFounderTask)(coordinationTaskId, invoke)
                            : await invoke();
                        resultText = toolResultText(result);
                    }
                    catch (error) {
                        resultText = `Error: ${error instanceof Error ? error.message : String(error)}`;
                    }
                }
                if (call.name === 'founder-edit-file'
                    && typeof parsedInput.filePath === 'string'
                    && !resultText.startsWith('Error:')) {
                    editedPaths.add(parsedInput.filePath.replaceAll('\\', '/').replace(/^\.\//, ''));
                }
                if (call.name === 'founder-run-command'
                    && typeof parsedInput.command === 'string'
                    && (0, verified_solution_memory_1.isVerificationCommand)(parsedInput.command)
                    && /\[exit code 0\]\s*$/.test(resultText)) {
                    passedChecks.push({
                        command: parsedInput.command,
                        result: 'passed',
                    });
                }
                gatewayMessages.push({
                    role: 'tool',
                    name: call.name,
                    tool_call_id: call.id,
                    content: resultText,
                });
            }
            if (!personalProfile && autoSelected && !escalationReason) {
                const detected = (0, auto_escalation_1.founderAutoEscalationReason)(gatewayMessages);
                if (detected) {
                    escalationReason = detected;
                    alias = (0, models_1.findModelAlias)('founder-os-reasoning') ?? alias;
                    stream.progress(`Founder Auto: escalating to Pro (${detected.replace('_', ' ')})`);
                }
            }
        }
        if (!completed && !token.isCancellationRequested) {
            stream.markdown('\n\n_Founder OS stopped after the tool-turn safety limit._');
        }
        providerCompleted = completed && !token.isCancellationRequested;
        activitySummary = finalAnswer;
        activityEditedFiles = [...editedPaths];
        activityChecks = passedChecks.map((check) => check.command);
        const completionReceipt = (0, completion_evidence_1.evaluateFounderCompletionEvidence)({
            mode: workMode,
            goal: prompt,
            finalAnswer,
            requestCompleted: providerCompleted,
            editedFiles: activityEditedFiles,
            passedChecks: activityChecks,
        });
        activityVerification = completionReceipt;
        stream.markdown((0, completion_evidence_1.renderFounderCompletionReceipt)(completionReceipt));
        ok = providerCompleted && completionReceipt.verdict === 'passed';
        if (!ok && providerCompleted) {
            errorMessage = `Verification incomplete: ${completionReceipt.missing.join('; ')}`;
            activitySummary = errorMessage;
        }
        if (ok && !usedTools && cacheInput && reusableAnswer) {
            deps.resultCache?.put(cacheInput, reusableAnswer, reusableTokenEstimate);
        }
        if (ok
            && finalAnswer
            && editedPaths.size > 0
            && passedChecks.length > 0
            && workspaceId) {
            await deps.projectContext?.refresh(true);
            const affectedFiles = deps.projectContext?.fileHashes([...editedPaths]) ?? [];
            if (affectedFiles.length === editedPaths.size) {
                const remembered = deps.solutionMemory?.remember({
                    workspaceId,
                    goal: prompt,
                    summary: finalAnswer,
                    commit: deps.projectContext?.headCommit() ?? null,
                    affectedFiles,
                    checks: passedChecks,
                });
                if (remembered) {
                    stream.markdown('\n\n---\n**Founder memory** | verified solution pattern saved locally');
                }
            }
        }
        if (ok && escalationReason) {
            stream.markdown(`\n\n---\n**Founder Auto escalation** | Pro | ${escalationReason.replace('_', ' ')}`);
        }
        if (providerCompleted) {
            const latencyMs = Date.now() - requestStartedAt;
            const route = personalProfile
                ? personalProfile.kind === 'ollama'
                    ? `Local | ${personalProfile.name} | ${personalProfile.model} | outside managed quota`
                    : `Personal AI | ${personalProfile.name} | ${personalProfile.model} | outside managed quota`
                : `Founder managed | ${activityProvider ?? 'DeepSeek'} | ${activityProviderModel ?? alias.id}`;
            stream.markdown(`\n\n---\n**Founder route** | ${route} | ${latencyMs.toLocaleString()} ms`);
        }
        const providerUsage = providerUsageEvidence.value;
        if (providerCompleted && providerUsage) {
            const cacheRate = providerUsage.promptTokens > 0
                ? Math.round((providerUsage.cachedInputTokens / providerUsage.promptTokens) * 10_000) / 100
                : 0;
            stream.markdown(`\n\n**${personalProfile ? 'Provider' : 'DeepSeek'} cache evidence** | ${providerUsage.cachedInputTokens.toLocaleString()} hit | ${providerUsage.uncachedInputTokens.toLocaleString()} miss | ${cacheRate}% hit rate | ${providerUsage.outputTokens.toLocaleString()} output`);
        }
        if (providerCompleted && inputCostComparison && inputCostComparison.avoidedInputTokens > 0) {
            stream.markdown(`\n\n**Estimated input comparison** | ${inputCostComparison.avoidedInputTokens.toLocaleString()} fewer input tokens | $${inputCostComparison.avoidedUsd.toFixed(6)} USD avoided | baseline: same request with full context and uncached ${activityProviderModel ?? 'DeepSeek'} input | ${inputCostComparison.priceVersion}`);
        }
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
        deps.onRequestEnd?.(selectedModelId, ok, errorMessage);
        deps.projectActivity?.complete(activityId, {
            status: token.isCancellationRequested ? 'cancelled' : ok ? 'completed' : 'failed',
            summary: ok ? activitySummary : errorMessage ?? 'Founder request did not complete.',
            provider: activityProvider,
            providerModel: activityProviderModel,
            editedFiles: activityEditedFiles,
            checks: activityChecks,
            verification: activityVerification,
        });
    }
    if (!ok && errorMessage) {
        stream.markdown(providerCompleted
            ? `\n\n_Founder verification incomplete: ${errorMessage.replace(/^Verification incomplete:\s*/i, '')}_`
            : `\n\n_Founder OS request failed: ${errorMessage}_`);
    }
}
function personalProviderUserMessage(profile, status) {
    if (status === 0)
        return `${profile.name} could not be reached. Test the profile in Founder Settings.`;
    if (status === 401 || status === 403)
        return `${profile.name} rejected its saved credential. Update and test the profile in Founder Settings.`;
    if (status === 429)
        return `${profile.name} is rate limited. Try again shortly or select another profile.`;
    if (status >= 500)
        return `${profile.name} is temporarily unavailable (HTTP ${status}). Your local files are safe.`;
    return `${profile.name} rejected this request (HTTP ${status}). Test the profile and model ID in Founder Settings.`;
}
//# sourceMappingURL=chat-participant.js.map