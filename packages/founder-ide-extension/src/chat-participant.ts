/**
 * `@Founder OS` chat participant — VS Code 1.93+ stable ChatParticipant API.
 *
 * It streams responses directly from the Founder OS gateway into the Chat view
 * and uses VS Code's native tool confirmation flow for workspace reads,
 * reviewed edits, and visible commands.
 */
import * as vscode from 'vscode';
import { findModelAlias, FOUNDER_OS_MODELS } from './models';
import type { ProfileManager } from './profile-manager';
import { buildSystemPrompt } from './memory';
import type { FounderOsCredentials } from './credentials';
import { authorizationHeaderFromCredentials, proxyBaseUrl } from './credentials';
import { CostTracker } from './cost-tracker';
import {
  type GatewayMessage,
  type GatewayClient,
  type GatewayFounderOsMetadata,
  type GatewayToolCall,
  callGateway,
  gatewayUserMessage,
} from './gateway-client';
import { FOUNDER_TOOL_NAMES } from './tool-names';
import { composeFounderSystemPrompt, estimateTokensFromText, planPromptEfficiency } from './prompt-efficiency';
import { runWithFounderTask } from './agent-task-context';
import type { FounderSafeResultCache } from './safe-result-cache';
import type { WorkspaceCacheContext } from './workspace-context-state';
import { normalizeFounderAgentMode } from './founder-agent-mode';
import {
  founderAutoEscalationReason,
  type FounderAutoEscalationReason,
} from './auto-escalation';
import {
  isVerificationCommand,
  type FounderVerifiedSolutionMemory,
  type VerifiedSolutionCheck,
} from './verified-solution-memory';
import type { FounderProjectActivityStore } from './project-activity';

export interface ParticipantDeps {
  creds: FounderOsCredentials;
  profileManager: ProfileManager;
  costTracker: CostTracker;
  onRequestStart?: (modelId: string) => void;
  onMetadata?: (meta: GatewayFounderOsMetadata) => void;
  onCacheHit?: (estimatedTokensAvoided: number) => void;
  onRequestEnd?: (
    modelId: string,
    ok: boolean,
    errorMessage?: string,
  ) => void;
  coordination?: {
    begin(prompt: string, provider: string, mode?: 'focus' | 'team'): string;
    contextFor(taskId: string): string;
    end(taskId: string): void;
  };
  projectContext?: {
    contextFor(prompt: string): string;
    cacheContextFor(prompt: string): WorkspaceCacheContext | null;
    workspaceIdValue(): string | null;
    allFileHashes(): Array<{ path: string; sha256: string }>;
    fileHashes(paths: string[]): Array<{ path: string; sha256: string }>;
    headCommit(): string | null;
    refresh(force?: boolean): Promise<void>;
  };
  resultCache?: FounderSafeResultCache;
  solutionMemory?: FounderVerifiedSolutionMemory;
  projectActivity?: FounderProjectActivityStore;
}

const MAX_TOOL_TURNS = 8;

function availableFounderTools(): readonly vscode.LanguageModelToolInformation[] {
  const tools = (vscode.lm as unknown as {
    tools?: readonly vscode.LanguageModelToolInformation[];
  }).tools;
  return [...(tools ?? [])]
    .filter((tool) => FOUNDER_TOOL_NAMES.has(tool.name))
    .sort((left, right) => left.name.localeCompare(right.name));
}

function toolResultText(result: vscode.LanguageModelToolResult): string {
  const parts: string[] = [];
  for (const part of result.content) {
    if (
      part &&
      typeof part === 'object' &&
      'value' in part &&
      typeof (part as { value?: unknown }).value === 'string'
    ) {
      parts.push((part as { value: string }).value);
    } else {
      try {
        parts.push(JSON.stringify(part));
      } catch {
        parts.push(String(part));
      }
    }
  }
  return parts.join('\n').slice(0, 40_000);
}

function parseToolInput(argumentsJson: string): object {
  try {
    const parsed = JSON.parse(argumentsJson) as unknown;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as object;
    }
  } catch {
    // The tool receives a visible error result below.
  }
  return { rawArguments: argumentsJson };
}

export function registerFounderOsChatParticipant(
  context: vscode.ExtensionContext,
  deps: ParticipantDeps,
): vscode.ChatParticipant | undefined {
  let participant: vscode.ChatParticipant | undefined;
  try {
    participant = vscode.chat.createChatParticipant(
      'founder-os.chat',
      (request, chatContext, stream, token) =>
        handleParticipantRequest(request, chatContext, stream, deps, token),
    );
  } catch {
    // Another extension may have claimed the id. Non-fatal.
    return undefined;
  }
  participant.iconPath = new vscode.ThemeIcon('sparkle');
  context.subscriptions.push(participant);
  return participant;
}

async function handleParticipantRequest(
  request: vscode.ChatRequest,
  _context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  deps: ParticipantDeps,
  token: vscode.CancellationToken,
): Promise<void> {
  const prompt = request.prompt ?? '';
  if (prompt.trim().length === 0) {
    stream.markdown('Type a message and I’ll route it through your Founder OS gateway.');
    return;
  }

  // Resolve the alias for the active execution profile (or `/code` slash cmd).
  const desiredAlias = deps.profileManager.alias;
  let alias = desiredAlias ?? FOUNDER_OS_MODELS[0];
  if (request.command === 'code') {
    const codeModel = findModelAlias('founder-os-code');
    if (codeModel) alias = codeModel;
  }
  const autoSelected = alias.id === 'founder-os-auto';
  let escalationReason: FounderAutoEscalationReason | null = autoSelected
    ? founderAutoEscalationReason([{ role: 'user', content: prompt }])
    : null;
  if (escalationReason) {
    alias = findModelAlias('founder-os-reasoning') ?? alias;
  }
  deps.onRequestStart?.(alias.id);
  const agentMode = normalizeFounderAgentMode(
    vscode.workspace.getConfiguration('founderOs').get<string>('agentMode'),
  );
  const coordinationTaskId = deps.coordination?.begin(prompt, alias.id, agentMode);

  // Build the system prompt with Memory Engine context.
  let memoryText = '';
  try {
    const memory = await buildSystemPrompt(deps.creds, token);
    if (memory.hasMemory && memory.text.length > 0) memoryText = memory.text;
  } catch {
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
    ? deps.projectActivity?.begin(workspaceId, prompt, alias.id) ?? null
    : null;
  const priorSolutions = workspaceId
    ? deps.solutionMemory?.contextFor(
        workspaceId,
        prompt,
        deps.projectContext?.allFileHashes() ?? [],
      ) ?? ''
    : '';
  const identity = 'You are Founder OS, the founder\'s AI pair-programmer routed via their own gateway. Inspect the workspace before changing it. Use the available tools to make requested code changes and verify them; do not merely describe work that can be completed locally. Be concise and direct.';
  const systemContent = composeFounderSystemPrompt({
    identity,
    memory: memoryText,
    projectContext: projectContextText,
    additionalStableContext: priorSolutions,
    coordination: coordinationText,
  });

  const gatewayMessages: GatewayMessage[] = [
    { role: 'system', content: systemContent },
    { role: 'user', content: prompt },
  ];

  const cacheInput = cacheContext
    ? { prompt, model: alias.id, context: cacheContext }
    : null;
  const cached = cacheInput ? deps.resultCache?.get(cacheInput) : null;
  if (cached) {
    stream.markdown(cached.text);
    stream.markdown(
      `\n\n---\n**Founder reuse** | safe read-only result | no provider request | ~${cached.estimatedTokensAvoided.toLocaleString()} tokens avoided (estimated)`,
    );
    if (coordinationTaskId) deps.coordination?.end(coordinationTaskId);
    deps.onRequestEnd?.(alias.id, true);
    deps.onCacheHit?.(cached.estimatedTokensAvoided);
    deps.projectActivity?.complete(activityId, {
      status: 'reused',
      summary: 'Reused a matching read-only result after relevant context hashes were verified.',
      estimatedTokensAvoided: cached.estimatedTokensAvoided,
    });
    return;
  }

  const client: GatewayClient = {
    baseUrl: proxyBaseUrl(deps.creds.apiBaseUrl),
    bearer: authorizationHeaderFromCredentials(deps.creds),
  };

  const cfg = vscode.workspace.getConfiguration('founderOs');
  const timeoutMs = cfg.get<number>('requestTimeoutMs') ?? 120_000;

  let ok = false;
  let errorMessage: string | undefined;
  let activitySummary = '';
  let activityProvider: string | null = null;
  let activityProviderModel: string | null = null;
  let activityEditedFiles: string[] = [];
  let activityChecks: string[] = [];
  try {
    const tools = availableFounderTools();
    let completed = false;
    let usedTools = false;
    let reusableAnswer = '';
    let reusableTokenEstimate = 0;
    const editedPaths = new Set<string>();
    const passedChecks: VerifiedSolutionCheck[] = [];
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
      const toolCalls: GatewayToolCall[] = [];
      let assistantText = '';
      const efficiency = planPromptEfficiency(gatewayMessages);
      await callGateway(
        client,
        {
          model: alias.id,
          messages: efficiency.messages,
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
          metadata: {
            founder_memory_included: memoryText.length > 0,
            prompt_efficiency: efficiency.estimate,
            ...(escalationReason ? { founder_auto_escalation: escalationReason } : {}),
          },
        },
        {
          onToken: (delta) => {
            if (token.isCancellationRequested) return;
            assistantText += delta;
            stream.markdown(delta);
          },
          onToolCall: (call) => toolCalls.push(call),
          onMetadata: (meta) => {
            activityProvider = typeof meta.provider === 'string' ? meta.provider : activityProvider;
            activityProviderModel = typeof meta.model === 'string' ? meta.model : activityProviderModel;
            deps.onMetadata?.(meta);
          },
          onError: (status, body) => {
            errorMessage = gatewayUserMessage(status, body);
          },
        },
        token,
      );

      if (toolCalls.length === 0) {
        completed = true;
        reusableAnswer = assistantText;
        reusableTokenEstimate = efficiency.estimate.sentTokens + estimateTokensFromText(assistantText);
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
        let resultText: string;
        const parsedInput = parseToolInput(call.arguments) as Record<string, unknown>;
        if (!FOUNDER_TOOL_NAMES.has(call.name)) {
          resultText = `Error: tool "${call.name}" is not available.`;
        } else {
          stream.progress(`Founder OS: ${call.name}`);
          try {
            const invoke = () => vscode.lm.invokeTool(
              call.name,
              {
                input: parsedInput,
                toolInvocationToken: request.toolInvocationToken,
              },
              token,
            );
            const result = coordinationTaskId
              ? await runWithFounderTask(coordinationTaskId, invoke)
              : await invoke();
            resultText = toolResultText(result);
          } catch (error) {
            resultText = `Error: ${
              error instanceof Error ? error.message : String(error)
            }`;
          }
        }
        if (
          call.name === 'founder-edit-file'
          && typeof parsedInput.filePath === 'string'
          && !resultText.startsWith('Error:')
        ) {
          editedPaths.add(
            parsedInput.filePath.replaceAll('\\', '/').replace(/^\.\//, ''),
          );
        }
        if (
          call.name === 'founder-run-command'
          && typeof parsedInput.command === 'string'
          && isVerificationCommand(parsedInput.command)
          && /\[exit code 0\]\s*$/.test(resultText)
        ) {
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
      if (autoSelected && !escalationReason) {
        const detected = founderAutoEscalationReason(gatewayMessages);
        if (detected) {
          escalationReason = detected;
          alias = findModelAlias('founder-os-reasoning') ?? alias;
          stream.progress(`Founder Auto: escalating to Pro (${detected.replace('_', ' ')})`);
        }
      }
    }

    if (!completed && !token.isCancellationRequested) {
      stream.markdown('\n\n_Founder OS stopped after the tool-turn safety limit._');
    }
    ok = completed && !token.isCancellationRequested;
    activitySummary = finalAnswer;
    activityEditedFiles = [...editedPaths];
    activityChecks = passedChecks.map((check) => check.command);
    if (ok && !usedTools && cacheInput && reusableAnswer) {
      deps.resultCache?.put(cacheInput, reusableAnswer, reusableTokenEstimate);
    }
    if (
      ok
      && finalAnswer
      && editedPaths.size > 0
      && passedChecks.length > 0
      && workspaceId
    ) {
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
          stream.markdown(
            '\n\n---\n**Founder memory** | verified solution pattern saved locally',
          );
        }
      }
    }
    if (ok && escalationReason) {
      stream.markdown(
        `\n\n---\n**Founder Auto escalation** | Pro | ${escalationReason.replace('_', ' ')}`,
      );
    }
  } catch (err) {
    errorMessage = err instanceof Error ? err.message : String(err);
    if (!token.isCancellationRequested) {
      stream.markdown(`\n\n_${errorMessage}_`);
    }
    return;
  } finally {
    if (coordinationTaskId) deps.coordination?.end(coordinationTaskId);
    deps.onRequestEnd?.(alias.id, ok, errorMessage);
    deps.projectActivity?.complete(activityId, {
      status: token.isCancellationRequested ? 'cancelled' : ok ? 'completed' : 'failed',
      summary: ok ? activitySummary : errorMessage ?? 'Founder request did not complete.',
      provider: activityProvider,
      providerModel: activityProviderModel,
      editedFiles: activityEditedFiles,
      checks: activityChecks,
    });
  }

  if (!ok && errorMessage) {
    stream.markdown(`\n\n_Founder OS request failed: ${errorMessage}_`);
  }
}

/** Re-export so extension.ts can import from one place. */
export { findModelAlias };
