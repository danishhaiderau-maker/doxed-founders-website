/*--------------------------------------------------------------------------------------
 *  sendFounderOs.ts - Founder OS Gateway rewire for Void (Phase 5 step 5)
 *
 *  This module replaces Void's per-provider LLM dispatch (OpenAI, Anthropic,
 *  Gemini, Ollama, ...) with a single call to the Founder OS Gateway:
 *
 *    POST {apiBaseUrl}/api/v1/chat/completions   (stream: true, SSE)
 *    Authorization: Bearer fos_{nodeId}:{nodeToken}
 *
 *  It ports the logic from packages/founder-ide-extension/src/gateway-client.ts
 *  (SSE parsing + the founderOs metadata line) and credentials.ts (vault
 *  discovery at ~/FounderVault/node-config.json) into Void's Electron main
 *  process environment, where sendLLMMessage.ts runs.
 *
 *  Feature -> Gateway model alias mapping (design report section 4.3):
 *    Autocomplete (FIM) -> founder-os-fast
 *    Edit / Ctrl+K      -> founder-os-code
 *    Chat (normal)      -> founder-os-auto
 *    Chat (agent)       -> founder-os-reasoning
 *    SCM / other        -> founder-os-fast
 *
 *  License: Apache-2.0 (Void upstream) + MIT (Founder OS additions).
 *--------------------------------------------------------------------------------------*/

// We are in the Electron main process, which provides a node-style require
// for 'fs', 'os', 'path', and the global fetch (Node 18+/Electron 28+).
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

import type {
	LLMChatMessage,
	LLMFIMMessage,
	OnText,
	OnFinalMessage,
	OnError,
	RawToolCallObj,
	AnthropicReasoning,
} from '../../common/sendLLMMessageTypes.js';
import type { ModelSelection, SettingsOfProvider } from '../../common/voidSettingsTypes.js';
import type { ChatMode } from '../../common/voidSettingsTypes.js';

// ---------------------------------------------------------------------------
// Credential discovery - mirrors credentials.ts. Reads the vault file that
// Founder Node writes on pair. Returns null if unpaired (which makes
// founderOsEnabled() false, so Void falls back to its own provider dispatch).
// ---------------------------------------------------------------------------
interface NodeConfigFile {
	apiBaseUrl: string;
	nodeId: string;
	nodeToken: string;
}

function nodeConfigPath(): string {
	return path.join(os.homedir(), 'FounderVault', 'node-config.json');
}

function readVaultConfig(): NodeConfigFile | null {
	try {
		const file = nodeConfigPath();
		if (!fs.existsSync(file)) return null;
		const raw = fs.readFileSync(file, 'utf8');
		const parsed = JSON.parse(raw) as Partial<NodeConfigFile>;
		if (
			typeof parsed.apiBaseUrl !== 'string' ||
			typeof parsed.nodeId !== 'string' ||
			typeof parsed.nodeToken !== 'string'
		) {
			return null;
		}
		return {
			apiBaseUrl: parsed.apiBaseUrl,
			nodeId: parsed.nodeId,
			nodeToken: parsed.nodeToken,
		};
	} catch {
		return null;
	}
}

/**
 * True when a Founder Node vault is paired. sendLLMMessage.ts checks this
 * before every request: if paired, route to the Gateway; if not, fall through
 * to Void's normal provider dispatch (keeps the editor usable unpaired).
 *
 * Can be forced off with FOUNDER_OS_GATEWAY=0 for debugging.
 */
export function founderOsEnabled(): boolean {
	if (process.env.FOUNDER_OS_GATEWAY === '0') return false;
	return readVaultConfig() !== null;
}

function proxyBaseUrl(apiBaseUrl: string): string {
	return `${apiBaseUrl.replace(/\/$/, '')}/api/v1`;
}

function bearerFromVault(cfg: NodeConfigFile): string {
	return `fos_${cfg.nodeId}:${cfg.nodeToken}`;
}

// ---------------------------------------------------------------------------
// Feature -> model alias mapping (design report section 4.3).
// loggingName values come from the callers:
//   'Autocomplete'                          -> FIM -> founder-os-fast
//   'Edit (Writeover) - <from>'             -> Ctrl+K -> founder-os-code
//   'Edit (Search/Replace) - <from>'        -> Ctrl+K -> founder-os-code
//   'Chat - <chatMode>'                     -> chat -> founder-os-auto or -reasoning
//   'VoidSCM - Commit Message'              -> founder-os-fast
// ---------------------------------------------------------------------------
function aliasForFeature(loggingName: string, messagesType: 'chatMessages' | 'FIMMessage', chatMode?: ChatMode | null): string {
	// Autocomplete is always FIM and always the fast tier.
	if (messagesType === 'FIMMessage') return 'founder-os-fast';
	if (loggingName.startsWith('Autocomplete')) return 'founder-os-fast';

	// Agent chat mode -> reasoning tier; normal/gather chat -> auto.
	if (loggingName.startsWith('Chat')) {
		if (chatMode === 'agent') return 'founder-os-reasoning';
		return 'founder-os-auto';
	}

	// Inline edit (Ctrl+K) -> code tier.
	if (loggingName.startsWith('Edit')) return 'founder-os-code';

	// SCM commit message + anything else -> fast/cheap.
	return 'founder-os-fast';
}

// ---------------------------------------------------------------------------
// SSE parsing - ports pumpSseStream/handleSseEvent from gateway-client.ts.
// Emits content deltas via onText, and routes the founderOs metadata pre-line
// to the console (a future UI hook can surface tier/provider/cost).
// ---------------------------------------------------------------------------
interface FounderOsMetadata {
	requestId?: string;
	tier?: string;
	provider?: string;
	model?: string;
	ddollarCost?: number;
	[k: string]: unknown;
}

function findEventBoundary(buf: string): number {
	const lf = buf.indexOf('\n\n');
	const crlf = buf.indexOf('\r\n\r\n');
	if (lf === -1) return crlf;
	if (crlf === -1) return lf;
	return Math.min(lf, crlf);
}

interface StreamHandlers {
	onDelta: (delta: string) => void;
	onMetadata?: (meta: FounderOsMetadata) => void;
}

function handleSseEvent(rawEvent: string, handlers: StreamHandlers): void {
	const dataLines: string[] = [];
	for (const line of rawEvent.split(/\r?\n/)) {
		if (line.startsWith('data:')) {
			dataLines.push(line.slice(5).replace(/^ /, ''));
		}
	}
	if (dataLines.length === 0) return;
	const payload = dataLines.join('\n').trim();
	if (payload === '[DONE]' || payload.length === 0) return;

	try {
		const evt = JSON.parse(payload) as Record<string, unknown>;
		// Non-standard Founder OS metadata pre-line (ai-proxy.controller.ts).
		if (evt && typeof evt === 'object' && 'founderOs' in evt) {
			handlers.onMetadata?.(evt.founderOs as FounderOsMetadata);
			return;
		}
		const choices = evt.choices as Array<{ delta?: { content?: string } }> | undefined;
		const delta = choices?.[0]?.delta?.content;
		if (typeof delta === 'string' && delta.length > 0) {
			handlers.onDelta(delta);
		}
	} catch {
		/* ignore malformed/keepalive chunk */
	}
}

async function pumpSseStream(
	body: ReadableStream<Uint8Array> | NodeJS.ReadableStream,
	handlers: StreamHandlers,
	signal: AbortSignal,
): Promise<void> {
	// The Gateway returns a web ReadableStream via fetch; handle both web and
	// node stream shapes defensively.
	const reader: { read(): Promise<{ done: boolean; value?: Uint8Array | Buffer }> } =
		(body as ReadableStream<Uint8Array>).getReader
			? (body as ReadableStream<Uint8Array>).getReader()
			: (() => {
				const nodeStream = body as NodeJS.ReadableStream;
				return {
					read: () =>
						new Promise<{ done: boolean; value?: Buffer }>((resolve, reject) => {
							const onData = (chunk: Buffer) => { nodeStream.removeListener('data', onData); nodeStream.removeListener('end', onEnd); nodeStream.removeListener('error', onError); resolve({ done: false, value: chunk }); };
							const onEnd = () => { nodeStream.removeListener('data', onData); nodeStream.removeListener('end', onEnd); nodeStream.removeListener('error', onError); resolve({ done: true }); };
							const onError = (err: unknown) => { nodeStream.removeListener('data', onData); nodeStream.removeListener('end', onEnd); nodeStream.removeListener('error', onError); reject(err); };
							nodeStream.once('data', onData);
							nodeStream.once('end', onEnd);
							nodeStream.once('error', onError);
						}),
				};
			})();

	const decoder = new TextDecoder();
	let buffer = '';

	try {
		for (;;) {
			if (signal.aborted) return;
			const { done, value } = await reader.read();
			if (done) break;
			if (!value) continue;
			buffer += decoder.decode(value as Uint8Array, { stream: true });

			let sepIndex: number;
			while ((sepIndex = findEventBoundary(buffer)) !== -1) {
				const rawEvent = buffer.slice(0, sepIndex);
				buffer = buffer.slice(sepIndex).replace(/^(\r?\n){2}/, '');
				handleSseEvent(rawEvent, handlers);
			}
		}
		if (buffer.trim().length > 0) handleSseEvent(buffer, handlers);
	} finally {
		try {
			// @ts-ignore - releaseLock exists on web readers only
			reader.releaseLock?.();
		} catch {
			/* noop */
		}
	}
}

// ---------------------------------------------------------------------------
// Message normalization. Void passes Anthropic/OpenAI/Gemini-shaped message
// unions; our Gateway speaks OpenAI chat. Flatten to {role, content} pairs.
// ---------------------------------------------------------------------------
interface OpenAiMsg {
	role: 'system' | 'user' | 'assistant' | 'tool';
	content: string;
	tool_call_id?: string;
}

function toOpenAiMessages(
	messages: LLMChatMessage[],
	separateSystemMessage: string | undefined,
): OpenAiMsg[] {
	const out: OpenAiMsg[] = [];
	if (separateSystemMessage) out.push({ role: 'system', content: separateSystemMessage });

	for (const m of messages) {
		const role = m.role as string;
		if (role === 'system' || role === 'developer' || role === 'user' || role === 'assistant' || role === 'tool') {
			const raw = m as any;
			const content = typeof raw.content === 'string'
				? raw.content
				: Array.isArray(raw.content)
					? (raw.content as Array<{ type: string; text?: string }>)
						.map((c) => (c.type === 'text' ? c.text ?? '' : ''))
						.join('')
					: '';
			const msg: OpenAiMsg = { role: role as OpenAiMsg['role'], content };
			// @ts-ignore - tool_call_id lives on the tool role variant
			if (role === 'tool' && raw.tool_call_id) msg.tool_call_id = raw.tool_call_id;
			out.push(msg);
		} else if (role === 'model') {
			// Gemini shape -> assistant
			const parts = (m as { parts?: Array<{ text?: string }> }).parts ?? [];
			out.push({ role: 'assistant', content: parts.map((p) => p.text ?? '').join('') });
		}
	}
	return out;
}

// ---------------------------------------------------------------------------
// Shared request params.
// ---------------------------------------------------------------------------
interface FounderOsCommonParams {
	onText: OnText;
	onFinalMessage: OnFinalMessage;
	onError: OnError;
	_setAborter: (fn: () => void) => void;
	loggingName: string;
}

async function gatewayFetch(
	body: Record<string, unknown>,
	onError: OnError,
): Promise<{ res: Response; controller: AbortController } | null> {
	const cfg = readVaultConfig();
	if (!cfg) {
		onError({ message: 'Founder OS: Founder Node not paired. Pair via Founder Node to enable AI.', fullError: null });
		return null;
	}
	const controller = new AbortController();
	const url = `${proxyBaseUrl(cfg.apiBaseUrl)}/chat/completions`;
	try {
		const res = await fetch(url, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'Authorization': `Bearer ${bearerFromVault(cfg)}`,
				'Accept': 'text/event-stream',
			},
			body: JSON.stringify(body),
			signal: controller.signal,
		});
		if (!res.ok || !res.body) {
			const text = await res.text().catch(() => '');
			onError({ message: `Founder OS gateway returned ${res.status}: ${text.slice(0, 500)}`, fullError: null });
			return null;
		}
		return { res, controller };
	} catch (err) {
		const message = err instanceof Error ? err.message : String(err);
		onError({ message: `Founder OS gateway network error: ${message}`, fullError: err instanceof Error ? err : null });
		return null;
	}
}

// ---------------------------------------------------------------------------
// Chat path (chatMessages) - used by Chat, Ctrl+K, Apply, SCM.
// ---------------------------------------------------------------------------
export interface FounderOsChatParams extends FounderOsCommonParams {
	messages: LLMChatMessage[];
	modelSelection: ModelSelection;
	settingsOfProvider: SettingsOfProvider;
	separateSystemMessage: string | undefined;
	chatMode: ChatMode | null;
}

export async function sendFounderOsChat(params: FounderOsChatParams): Promise<void> {
	const { messages, onText, onFinalMessage, onError, _setAborter, loggingName, separateSystemMessage, chatMode } = params;

	const model = aliasForFeature(loggingName, 'chatMessages', chatMode);
	const openAiMessages = toOpenAiMessages(messages, separateSystemMessage);

	const body: Record<string, unknown> = {
		model,
		messages: openAiMessages,
		stream: true,
		founder_os_metadata: true,
	};

	const got = await gatewayFetch(body, onError);
	if (!got) return;
	const { res, controller } = got;
	_setAborter(() => controller.abort());

	let fullText = '';
	let fullReasoning = '';

	await pumpSseStream(res.body as ReadableStream<Uint8Array>, {
		onDelta: (delta) => {
			fullText += delta;
			onText({ fullText, fullReasoning, toolCall: undefined });
		},
		onMetadata: (meta) => {
			// Route transparency. A future UI hook can surface this in the
			// chat thread status slot; for now log it so it's observable.
			console.log(`[Founder OS] ${meta.tier ?? '?'}/${meta.provider ?? '?'}/${meta.model ?? '?'} cost=${meta.ddollarCost ?? '?'} D$ req=${meta.requestId ?? '?'}`);
		},
	}, controller.signal);

	if (!fullText && !fullReasoning) {
		onError({ message: 'Founder OS: Response from gateway was empty.', fullError: null });
		return;
	}

	const finalParams: Parameters<OnFinalMessage>[0] = {
		fullText,
		fullReasoning,
		anthropicReasoning: null,
	};
	onFinalMessage(finalParams);

	// satisfy RawToolCallObj import (unused but keeps the type graph intact)
	void (null as unknown as RawToolCallObj);
	void (null as unknown as AnthropicReasoning);
}

// ---------------------------------------------------------------------------
// FIM path (autocomplete) - uses the founder-os-fast alias. The Gateway
// recognizes the fim field and routes to a FIM-capable model. We pass prefix/
// suffix/stop straight through.
// ---------------------------------------------------------------------------
export interface FounderOsFIMParams extends FounderOsCommonParams {
	messages: LLMFIMMessage;
}

export async function sendFounderOsFIM(params: FounderOsFIMParams): Promise<void> {
	const { messages, onFinalMessage, onError, _setAborter, loggingName } = params;

	const model = aliasForFeature(loggingName, 'FIMMessage');
	const body: Record<string, unknown> = {
		model,
		messages: [],
		stream: true,
		// Gateway FIM shape (design report section 5.2).
		fim: {
			prefix: messages.prefix,
			suffix: messages.suffix,
			stop: messages.stopTokens,
		},
		max_tokens: 300,
	};

	const got = await gatewayFetch(body, onError);
	if (!got) return;
	const { res, controller } = got;
	_setAborter(() => controller.abort());

	// FIM doesn't stream incremental text in Void's design (onText is unused
	// for FIMMessage - see autocompleteService.ts); collect then onFinalMessage.
	let fullText = '';
	await pumpSseStream(res.body as ReadableStream<Uint8Array>, {
		onDelta: (delta) => { fullText += delta; },
	}, controller.signal);

	if (!fullText) {
		onError({ message: 'Founder OS: Autocomplete response from gateway was empty.', fullError: null });
		return;
	}
	onFinalMessage({ fullText, fullReasoning: '', anthropicReasoning: null });
}