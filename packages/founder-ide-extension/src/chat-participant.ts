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
  callGateway,
} from './gateway-client';

export interface ParticipantDeps {
  creds: FounderOsCredentials;
  profileManager: ProfileManager;
  costTracker: CostTracker;
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

  // Build the system prompt with Memory Engine context.
  let memoryText = '';
  try {
    const memory = await buildSystemPrompt(deps.creds, token);
    if (memory.hasMemory && memory.text.length > 0) memoryText = memory.text;
  } catch {
    /* memory must never block chat */
  }

  const systemContent = memoryText.length > 0
    ? `${memoryText}\n\nYou are Founder OS, the founder's AI pair-programmer. Be concise and direct.`
    : 'You are Founder OS, the founder\'s AI pair-programmer routed via their own gateway. Be concise and direct.';

  const gatewayMessages: GatewayMessage[] = [
    { role: 'system', content: systemContent },
    { role: 'user', content: prompt },
  ];

  const client: GatewayClient = {
    baseUrl: proxyBaseUrl(deps.creds.apiBaseUrl),
    bearer: authorizationHeaderFromCredentials(deps.creds),
  };

  const cfg = vscode.workspace.getConfiguration('founderOs');
  const timeoutMs = cfg.get<number>('requestTimeoutMs') ?? 120_000;

  let ok = false;
  let errorMessage: string | undefined;
  try {
    await callGateway(
      client,
      {
        model: alias.id,
        messages: gatewayMessages,
        executionProfile: alias.executionProfile,
        founderOsMetadata: true,
        timeoutMs,
      },
      {
        onToken: (delta) => {
          if (token.isCancellationRequested) return;
          stream.markdown(delta);
        },
        onMetadata: (meta) => {
          deps.costTracker?.record(meta);
        },
        onError: (_status, body) => {
          errorMessage = body.slice(0, 300);
        },
      },
      token,
    );
    ok = !token.isCancellationRequested;
  } catch (err) {
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

/** Re-export so extension.ts can import from one place. */
export { findModelAlias };
