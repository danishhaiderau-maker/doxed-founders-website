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
import * as vscode from 'vscode';
import {
  type FounderOsCredentials,
  authorizationHeaderFromCredentials,
  proxyBaseUrl,
} from './credentials';
import {
  FOUNDER_OS_MODELS,
  FOUNDER_OS_VENDOR,
  type FounderOsModelAlias,
  findModelAlias,
} from './models';
import {
  type GatewayMessage,
  callGateway,
} from './gateway-client';
import { buildSystemPrompt } from './memory';

type ChatInformation = vscode.LanguageModelChatInformation;

/** Convert our alias metadata into the VS Code `LanguageModelChatInformation` shape. */
function aliasToInfo(alias: FounderOsModelAlias): ChatInformation {
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
function messageContentToText(
  content: readonly vscode.LanguageModelInputPart[] | readonly unknown[],
): string {
  const parts: string[] = [];
  for (const part of content) {
    if (part instanceof vscode.LanguageModelTextPart) {
      parts.push(part.value);
    } else if (
      part &&
      typeof part === 'object' &&
      'value' in part &&
      typeof (part as { value: unknown }).value === 'string'
    ) {
      // Best-effort fallback for any other text-like part.
      parts.push((part as { value: string }).value);
    }
  }
  return parts.join('');
}

function roleToString(role: vscode.LanguageModelChatMessageRole): GatewayMessage['role'] {
  // The VS Code enum only has User (1) and Assistant (2). System prompts are
  // not carried as a role in `LanguageModelChatRequestMessage`; if a future
  // version adds System, we map it to 'user' as a safe fallback for now and
  // rely on the gateway / Memory Engine to manage system context.
  if (role === vscode.LanguageModelChatMessageRole.Assistant) return 'assistant';
  return 'user';
}

export interface FounderOsChatProviderEvents {
  /** Fired when a request starts — used by the status bar to show activity. */
  onRequestStart?: (modelId: string) => void;
  /** Fired when the gateway emits a `founderOs` metadata pre-line. */
  onMetadata?: (meta: {
    requestId?: string;
    tier?: string;
    provider?: string;
    model?: string;
    ddollarCost?: number;
  }) => void;
  /** Fired when a request completes (ok or cancelled). */
  onRequestEnd?: (modelId: string, ok: boolean, errorMessage?: string) => void;
}

export class FounderOsChatProvider
  implements vscode.LanguageModelChatProvider<ChatInformation>
{
  private readonly events: FounderOsChatProviderEvents;
  private readonly requestTimeoutMs: number;
  private readonly founderOsMetadata: boolean;
  private readonly creds: FounderOsCredentials;

  constructor(creds: FounderOsCredentials, events: FounderOsChatProviderEvents = {}) {
    this.events = events;
    this.creds = creds;
    const cfg = vscode.workspace.getConfiguration('founderOs');
    this.requestTimeoutMs = cfg.get<number>('requestTimeoutMs') ?? 120_000;
    // Metadata pre-line is opt-in. Server may not emit it yet; that's fine —
    // the parser just never sees a `founderOs` line and the cost stays hidden.
    this.founderOsMetadata = true;

    this.client = {
      baseUrl: proxyBaseUrl(creds.apiBaseUrl),
      bearer: authorizationHeaderFromCredentials(creds),
    };
  }

  private readonly client: { baseUrl: string; bearer: string };

  provideLanguageModelChatInformation(
    _options: { silent: boolean },
    _token: vscode.CancellationToken,
  ): vscode.ProviderResult<ChatInformation[]> {
    return FOUNDER_OS_MODELS.map(aliasToInfo);
  }

  async provideLanguageModelChatResponse(
    model: ChatInformation,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    options: vscode.ProvideLanguageModelChatResponseOptions,
    progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    token: vscode.CancellationToken,
  ): Promise<void> {
    const alias = findModelAlias(model.id) ?? FOUNDER_OS_MODELS[0];
    this.events.onRequestStart?.(alias.id);

    // Convert VS Code messages → OpenAI-compatible messages. VS Code always
    // sends a System message first when the Chat participant defines one; we
    // pass it through unchanged so Memory Engine / system prompt injection
    // (Phase 4) can prepend to it.
    const gatewayMessages: GatewayMessage[] = messages.map((m) => ({
      role: roleToString(m.role),
      content: messageContentToText(m.content),
      name: m.name,
    }));

    // Memory Engine injection (design report §8.3). Fetch project + founder
    // memory for the current workspace and prepend it as a system message.
    // Best-effort: never breaks chat if the endpoint is missing or slow.
    try {
      const memory = await buildSystemPrompt(this.creds, token);
      if (memory.hasMemory && memory.text.length > 0) {
        // If the first message is already a system message, merge so we don't
        // send two system blocks (some providers dislike that).
        if (gatewayMessages.length > 0 && gatewayMessages[0].role === 'system') {
          gatewayMessages[0] = {
            ...gatewayMessages[0],
            content: `${memory.text}\n\n${gatewayMessages[0].content ?? ''}`,
          };
        } else {
          gatewayMessages.unshift({ role: 'system', content: memory.text });
        }
      }
    } catch {
      // Memory fetch must never block the chat.
    }

    let ok = false;
    let errorMessage: string | undefined;
    try {
      // `options.modelOptions` is an opaque `{ name: any }` map from the
      // user's language-model settings. We pass a temperature through only if
      // it's a recognizable number.
      const temperature =
        typeof options.modelOptions?.temperature === 'number'
          ? (options.modelOptions.temperature as number)
          : undefined;

      await callGateway(
        this.client,
        {
          model: alias.id,
          messages: gatewayMessages,
          executionProfile: alias.executionProfile,
          founderOsMetadata: this.founderOsMetadata,
          timeoutMs: this.requestTimeoutMs,
          temperature,
        },
        {
          onToken: (delta) => {
            if (token.isCancellationRequested) return;
            progress.report(new vscode.LanguageModelTextPart(delta));
          },
          onMetadata: (meta) => this.events.onMetadata?.(meta),
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
        // Surface the error in-line in the chat so the user sees why it failed.
        progress.report(
          new vscode.LanguageModelTextPart(
            `\n\n_Founder OS gateway error: ${errorMessage}_`,
          ),
        );
      }
    } finally {
      this.events.onRequestEnd?.(alias.id, ok, errorMessage);
    }
  }

  provideTokenCount(
    _model: ChatInformation,
    text: string | vscode.LanguageModelChatRequestMessage,
    _token: vscode.CancellationToken,
  ): Thenable<number> {
    const str =
      typeof text === 'string'
        ? text
        : messageContentToText(text.content);
    // Rough OpenAI-style estimate (~4 chars/token). Good enough for context
    // window management; the gateway does the real accounting server-side.
    return Promise.resolve(Math.max(1, Math.ceil(str.length / 4)));
  }
}

/** Vendor ID used in `package.json` `contributes.languageModelChatProviders`. */
export const VENDOR = FOUNDER_OS_VENDOR;
