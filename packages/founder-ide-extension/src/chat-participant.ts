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
import * as vscode from 'vscode';
import { FOUNDER_OS_VENDOR, findModelAlias } from './models';
import type { ProfileManager } from './profile-manager';
import { buildSystemPrompt } from './memory';
import type { FounderOsCredentials } from './credentials';
import { CostTracker } from './cost-tracker';

/** Tool names registered by the extension that we expose to the model. */
const FOUNDER_TOOL_NAMES = [
  'founder.editFile',
  'founder.runCommand',
  'founder.readWorkspace',
] as const;

const MAX_TOOL_ROUNDS = 6;

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
  const profile = deps.profileManager.profile;
  const desiredAlias = deps.profileManager.alias;

  // Resolve a founder-os LanguageModelChat. Prefer the profile's alias; fall
  // back to any founder-os model if the alias isn't selectable (e.g. older API).
  const models = await vscode.lm.selectChatModels({ vendor: FOUNDER_OS_VENDOR });
  if (!models || models.length === 0) {
    stream.markdown(
      'Founder OS is not connected. Pair Founder Node (or set `founderOs.*` settings) and reload, then try again.',
    );
    return;
  }
  let model = models.find((m) => m.id === desiredAlias.id) ?? models[0];

  // Honour the `/code` slash command if present — force the coding alias.
  if (request.command === 'code') {
    const codeModel = models.find((m) => m.id === 'founder-os-code');
    if (codeModel) model = codeModel;
  }

  // Build the system prompt with Memory Engine context.
  const memory = await buildSystemPrompt(deps.creds, token);
  const systemMessage = vscode.LanguageModelChatMessage.User(
    memory.hasMemory && memory.text.length > 0
      ? `${memory.text}\n\nYou are Founder OS, the founder's AI pair-programmer. Be concise and direct.`
      : 'You are Founder OS, the founder\'s AI pair-programmer routed via their own gateway. Be concise and direct.',
  );

  const userMessage = vscode.LanguageModelChatMessage.User(request.prompt);

  // Gather tool references for the registered founder.* tools.
  const availableTools = vscode.lm.tools.filter((t) =>
    (FOUNDER_TOOL_NAMES as readonly string[]).includes(t.name),
  );
  const toolRefs: vscode.LanguageModelChatTool[] = availableTools.map((info) => ({
    name: info.name,
    description: info.description,
    inputSchema: info.inputSchema,
  }));

  const requestOptions: vscode.LanguageModelChatRequestOptions = {
    justification: 'Answering a Founder OS chat request via the founder\'s own gateway.',
    tools: toolRefs,
    toolMode: vscode.LanguageModelChatToolMode.Auto,
  };

  const messages = [systemMessage, userMessage];
  let rounds = 0;

  while (rounds < MAX_TOOL_ROUNDS) {
    rounds++;
    let response: vscode.LanguageModelChatResponse;
    try {
      response = await model.sendRequest(messages, requestOptions, token);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      stream.markdown(`\n\n_Founder OS request failed: ${msg}_`);
      return;
    }

    // Consume the stream, forwarding text to the chat and collecting tool calls.
    const toolCalls: vscode.LanguageModelToolCallPart[] = [];
    try {
      for await (const chunk of response.stream) {
        if (chunk instanceof vscode.LanguageModelTextPart) {
          stream.markdown(chunk.value);
        } else if (chunk instanceof vscode.LanguageModelToolCallPart) {
          toolCalls.push(chunk);
        }
      }
    } catch (err) {
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

    const resultParts: vscode.LanguageModelToolResultPart[] = [];
    for (const call of toolCalls) {
      try {
        const result = await vscode.lm.invokeTool(
          call.name,
          {
            toolInvocationToken: request.toolInvocationToken,
            input: call.input,
          } as vscode.LanguageModelToolInvocationOptions<object>,
          token,
        );
        // Surface a short note in the chat so the founder sees what happened.
        const firstText = result.content
          .find((c) => c instanceof vscode.LanguageModelTextPart) as
          | vscode.LanguageModelTextPart
          | undefined;
        stream.markdown(
          `\n\n_Tool \`${call.name}\` ran: ${(firstText?.value ?? '(ok)').slice(0, 200)}_\n\n`,
        );
        resultParts.push(
          new vscode.LanguageModelToolResultPart(call.callId, result.content),
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        stream.markdown(`\n\n_Tool \`${call.name}\` failed: ${msg}_\n\n`);
        resultParts.push(
          new vscode.LanguageModelToolResultPart(call.callId, [
            new vscode.LanguageModelTextPart(`Tool error: ${msg}`),
          ]),
        );
      }
    }
    messages.push(vscode.LanguageModelChatMessage.User(resultParts));
    // Loop again so the model can react to the tool results.
  }

  stream.markdown(
    `\n\n_Founder OS: reached the ${MAX_TOOL_ROUNDS}-round tool-use limit for this turn._`,
  );
}

/** Re-export so extension.ts can import from one place. */
export { findModelAlias };
