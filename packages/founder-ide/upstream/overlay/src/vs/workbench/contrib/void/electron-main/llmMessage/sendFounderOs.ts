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
	RawToolParamsObj,
	AnthropicReasoning,
} from '../../common/sendLLMMessageTypes.js';
import type { ModelSelection, SettingsOfProvider } from '../../common/voidSettingsTypes.js';
import type { ChatMode } from '../../common/voidSettingsTypes.js';
import { availableTools, type InternalToolInfo } from '../../common/prompt/prompts.js';
import {
	founderReviewTools,
	isFounderReviewReconciliation,
} from './founderIndependentReview.js';
import {
	nativeSkillForWorkMode,
	nativeSkillReceipt,
	nativeSkillSystem,
	nativeSkillToolTurnsUsed,
} from './founderNativeSkills.js';
import { beginNativeCoordination } from './founderNativeCoordination.js';
import {
	formatNativeTeamAdvice,
	latestFounderRequest,
	nativeTeamAdvisers,
	readNativeAgentMode,
	readNativeWorkMode,
	shouldAssembleNativeTeam,
	nativeWorkModeSystem,
	type FounderTeamAdviser,
} from './founderNativeTeam.js';
import {
	nativeAutoEscalationReason,
	stripUntrustedFounderRouteReceipts,
	type FounderNativeEscalationReason,
} from './founderNativeRouting.js';

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

function gatewayErrorMessage(status: number, responseBody: string): string {
	const normalized = responseBody.toLowerCase();
	const providerCredentialFailed =
		normalized.includes('api key') &&
		(normalized.includes('invalid') || normalized.includes('authentication'));

	if (providerCredentialFailed) {
		return 'The selected AI provider is unavailable. Open Founder Connections to repair it or choose another model.';
	}
	if (status === 401 || status === 403) {
		return 'Your Founder session needs to be renewed. Open the Founder panel and sign in again.';
	}
	if (status === 429) {
		return 'Your Founder AI allowance is temporarily unavailable. Check Founder Connections for usage and provider options.';
	}
	if (status >= 500) {
		return 'Founder AI is temporarily unavailable. Your workspace and local files are unaffected.';
	}
	return 'Founder could not send this request. Check the active model in Founder Connections.';
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
	routePolicy?: 'free_flash_only' | 'managed_auto';
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
	onReasoningDelta?: (delta: string) => void;
	onToolDelta?: (tool: {
		index: number;
		id?: string;
		function?: { name?: string; arguments?: string };
	}) => void;
	onMetadata?: (meta: FounderOsMetadata) => void;
	onDone?: () => void;
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
	if (payload === '[DONE]') {
		handlers.onDone?.();
		return;
	}
	if (payload.length === 0) return;

	try {
		const evt = JSON.parse(payload) as Record<string, unknown>;
		// Non-standard Founder OS metadata pre-line (ai-proxy.controller.ts).
		if (evt && typeof evt === 'object' && 'founderOs' in evt) {
			handlers.onMetadata?.(evt.founderOs as FounderOsMetadata);
			return;
		}
		const choices = evt.choices as Array<{
			delta?: {
				content?: string;
				reasoning_content?: string;
				tool_calls?: Array<{
					index: number;
					id?: string;
					function?: { name?: string; arguments?: string };
				}>;
			};
		}> | undefined;
		const choiceDelta = choices?.[0]?.delta;
		const delta = choiceDelta?.content;
		if (typeof delta === 'string' && delta.length > 0) {
			handlers.onDelta(delta);
		}
		const reasoningDelta = choiceDelta?.reasoning_content;
		if (typeof reasoningDelta === 'string' && reasoningDelta.length > 0) {
			handlers.onReasoningDelta?.(reasoningDelta);
		}
		for (const tool of choiceDelta?.tool_calls ?? []) handlers.onToolDelta?.(tool);
	} catch {
		/* ignore malformed/keepalive chunk */
	}
}

async function pumpSseStream(
	body: ReadableStream<Uint8Array> | NodeJS.ReadableStream,
	handlers: StreamHandlers,
	signal: AbortSignal,
): Promise<boolean> {
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
	let sawDone = false;
	const trackedHandlers: StreamHandlers = {
		...handlers,
		onDone: () => {
			sawDone = true;
			handlers.onDone?.();
		},
	};

	try {
		for (;;) {
			if (signal.aborted) return false;
			const { done, value } = await reader.read();
			if (done) break;
			if (!value) continue;
			buffer += decoder.decode(value as Uint8Array, { stream: true });

			let sepIndex: number;
			while ((sepIndex = findEventBoundary(buffer)) !== -1) {
				const rawEvent = buffer.slice(0, sepIndex);
				buffer = buffer.slice(sepIndex).replace(/^(\r?\n){2}/, '');
				handleSseEvent(rawEvent, trackedHandlers);
			}
		}
		if (buffer.trim().length > 0) handleSseEvent(buffer, trackedHandlers);
	} finally {
		try {
			// @ts-ignore - releaseLock exists on web readers only
			reader.releaseLock?.();
		} catch {
			/* noop */
		}
	}
	return sawDone;
}

// ---------------------------------------------------------------------------
// Message normalization. Void passes Anthropic/OpenAI/Gemini-shaped message
// unions; our Gateway speaks OpenAI chat. Flatten to {role, content} pairs.
// ---------------------------------------------------------------------------
interface OpenAiMsg {
	role: 'system' | 'user' | 'assistant' | 'tool';
	content: string;
	tool_call_id?: string;
	tool_calls?: Array<{
		id: string;
		type: 'function';
		function: { name: string; arguments: string };
	}>;
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
			if (role === 'assistant' && Array.isArray(raw.tool_calls)) msg.tool_calls = raw.tool_calls;
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
	onController?: (controller: AbortController) => void,
): Promise<{ res: Response; controller: AbortController } | null> {
	const cfg = readVaultConfig();
	if (!cfg) {
		onError({ message: 'Founder OS: Founder Node not paired. Pair via Founder Node to enable AI.', fullError: null });
		return null;
	}
	const controller = new AbortController();
	onController?.(controller);
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
			onError({
				message: gatewayErrorMessage(res.status, text),
				fullError: null,
			});
			return null;
		}
		return { res, controller };
	} catch (err) {
		onError({
			message: 'Founder could not reach the AI gateway. Check your connection from the Founder panel.',
			fullError: err instanceof Error ? err : null,
		});
		return null;
	}
}

function inlineReceiptValue(value: unknown, fallback = '?'): string {
	if (typeof value !== 'string' || !value.trim()) return fallback;
	return value.trim().replace(/[\r\n`|]/g, ' ').slice(0, 120);
}

function founderRouteReceipt(
	meta: FounderOsMetadata | undefined,
	latencyMs: number,
	escalationReason: FounderNativeEscalationReason | null,
): string {
	if (!meta) return '';
	const provider = inlineReceiptValue(meta.provider);
	const model = inlineReceiptValue(meta.model);
	const tier = inlineReceiptValue(meta.tier);
	const cost = typeof meta.ddollarCost === 'number' && Number.isFinite(meta.ddollarCost)
		? ` · ${meta.ddollarCost} D$`
		: '';
	const policy = meta.routePolicy === 'free_flash_only' ? ' | Free: Flash' : '';
	const escalation = escalationReason
		? ` | Auto requested Pro: ${escalationReason.replace('_', ' ')}`
		: '';
	return `\n\n---\n**Founder route** · ${tier} · ${provider}/${model} · ${latencyMs} ms${cost}${policy}${escalation}`;
}

async function requestNativeTeamAdvice(
	adviser: FounderTeamAdviser,
	request: string,
	controllers: Set<AbortController>,
): Promise<{ adviser: FounderTeamAdviser; text: string }> {
	let registeredController: AbortController | undefined;
	const got = await gatewayFetch(
		{
			model: 'founder-os-fast',
			messages: [
				{ role: 'system', content: adviser.system },
				{ role: 'user', content: request },
			],
			stream: true,
			max_tokens: 450,
			founder_os_metadata: true,
			metadata: { founder_agent_role: adviser.id, founder_agent_mode: 'team' },
		},
		() => undefined,
		(controller) => {
			registeredController = controller;
			controllers.add(controller);
		},
	);
	if (!got) {
		if (registeredController) controllers.delete(registeredController);
		return { adviser, text: '' };
	}
	let text = '';
	try {
		const done = await pumpSseStream(
			got.res.body as ReadableStream<Uint8Array>,
			{ onDelta: (delta) => { text += delta; } },
			got.controller.signal,
		);
		return { adviser, text: done ? text : '' };
	} catch {
		return { adviser, text: '' };
	} finally {
		controllers.delete(got.controller);
	}
}

function gatewayTools(
	chatMode: ChatMode | null,
	mcpTools: InternalToolInfo[] | undefined,
	workMode: ReturnType<typeof readNativeWorkMode>,
	strictReadOnly = false,
	toolBudgetRemaining = Number.POSITIVE_INFINITY,
): Array<Record<string, unknown>> {
	if (workMode === 'ask' || toolBudgetRemaining <= 0) return [];
	const tools = availableTools(chatMode, mcpTools) ?? [];
	const skill = nativeSkillForWorkMode(workMode);
	const allowedTools = strictReadOnly
		? founderReviewTools(true, tools) ?? []
		: skill.toolPolicy === 'read_only'
		? tools.filter((tool) =>
			!/(?:apply|command|create|delete|edit|execute|insert|mkdir|move|patch|remove|rename|replace|run|shell|terminal|write)/i.test(tool.name),
		)
		: tools;
	return allowedTools.map((tool) => ({
		type: 'function',
		function: {
			name: tool.name,
			description: tool.description,
			parameters: {
				type: 'object',
				properties: Object.fromEntries(
					Object.entries(tool.params).map(([name, info]) => [name, { type: 'string', ...info }]),
				),
			},
		},
	}));
}

function completedToolCall(name: string, argumentsJson: string, id: string): RawToolCallObj | null {
	if (!name) return null;
	try {
		const parsed = JSON.parse(argumentsJson) as unknown;
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
		const rawParams = parsed as RawToolParamsObj;
		return {
			id,
			name,
			rawParams,
			doneParams: Object.keys(rawParams),
			isDone: true,
		};
	} catch {
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
	mcpTools: InternalToolInfo[] | undefined;
	coordination?: {
		threadId: string;
		workspacePath: string;
	};
}

export async function sendFounderOsChat(params: FounderOsChatParams): Promise<void> {
	const { messages, onText, onFinalMessage, onError, _setAborter, loggingName, modelSelection, separateSystemMessage, chatMode, mcpTools } = params;

	const openAiMessages = toOpenAiMessages(messages, separateSystemMessage);
	const isReviewReconciliation = isFounderReviewReconciliation(openAiMessages);
	const workMode = isReviewReconciliation ? 'plan' : readNativeWorkMode();
	const skill = nativeSkillForWorkMode(workMode);
	const skillToolTurnsUsed = nativeSkillToolTurnsUsed(openAiMessages);
	const skillToolBudgetRemaining = Math.max(0, skill.maxToolTurns - skillToolTurnsUsed);
	const requestedModel = modelSelection?.modelName;
	const escalationReason = requestedModel === 'founder-os-auto'
		? nativeAutoEscalationReason(openAiMessages)
		: null;
	const model = escalationReason
		? 'founder-os-reasoning'
		: typeof requestedModel === 'string' && requestedModel.startsWith('founder-os-')
			? requestedModel
			: aliasForFeature(loggingName, 'chatMessages', chatMode);
	openAiMessages.unshift({
		role: 'system',
		content: 'You are Founder AI inside Founder IDE. If asked what you are, identify yourself as Founder AI. Explain that Founder Auto chooses an eligible route and that the application shows the exact provider and model after the answer. Never write, imitate, quote, or predict a Founder route receipt; that trusted evidence is appended by the application. Never claim that you are merely a generic expert coding agent or that the product cannot identify its route.',
	});
	openAiMessages.splice(1, 0, {
		role: 'system',
		content: nativeWorkModeSystem(workMode),
	});
	openAiMessages.splice(2, 0, {
		role: 'system',
		content: nativeSkillSystem(skill),
	});
	if (isReviewReconciliation) {
		openAiMessages.splice(3, 0, {
			role: 'system',
			content: 'Founder Second brain reconciliation is read-only. Verify evidence, preserve disagreement, propose the smallest correction, and require normal approval before any later editing turn.',
		});
	}
	const coordination = params.coordination
		? beginNativeCoordination({
			threadId: params.coordination.threadId,
			workspacePath: params.coordination.workspacePath,
			provider: model,
			messages: openAiMessages,
		})
		: undefined;
	if (coordination?.context) {
		openAiMessages.splice(1, 0, { role: 'system', content: coordination.context });
	}
	const coordinationHeartbeat = coordination
		? setInterval(() => coordination.refresh(), 60_000)
		: undefined;
	coordinationHeartbeat?.unref?.();
	const activeControllers = new Set<AbortController>();
	let requestAborted = false;
	_setAborter(() => {
		requestAborted = true;
		for (const controller of activeControllers) controller.abort();
	});

	const founderRequest = latestFounderRequest(openAiMessages);
	if (shouldAssembleNativeTeam(readNativeAgentMode(), chatMode, founderRequest)) {
		const adviserResults = await Promise.all(
			nativeTeamAdvisers().map((adviser) =>
				requestNativeTeamAdvice(adviser, founderRequest, activeControllers),
			),
		);
		const handoff = formatNativeTeamAdvice(adviserResults);
		if (handoff) {
			const insertAt = coordination?.context ? 2 : 1;
			openAiMessages.splice(insertAt, 0, { role: 'system', content: handoff });
		}
	}
	if (requestAborted) {
		if (coordinationHeartbeat) clearInterval(coordinationHeartbeat);
		coordination?.settle();
		return;
	}

	const body: Record<string, unknown> = {
		model,
		messages: openAiMessages,
		stream: true,
		founder_os_metadata: true,
		metadata: {
			founder_skill: `${skill.id}@${skill.version}`,
			...(escalationReason ? { founder_auto_escalation: escalationReason } : {}),
			...(isReviewReconciliation ? { founder_second_brain_reconciliation: true } : {}),
		},
	};
	const tools = gatewayTools(
		chatMode,
		mcpTools,
		workMode,
		isReviewReconciliation,
		skillToolBudgetRemaining,
	);
	if (tools.length > 0) {
		body.tools = tools;
		body.tool_choice = 'auto';
	}

	const got = await gatewayFetch(body, onError, (controller) => activeControllers.add(controller));
	if (!got) {
		if (coordinationHeartbeat) clearInterval(coordinationHeartbeat);
		coordination?.fail();
		return;
	}
	const { res, controller } = got;

	const startedAt = Date.now();
	let fullText = '';
	let fullReasoning = '';
	let routeMetadata: FounderOsMetadata | undefined;
	let toolName = '';
	let toolId = '';
	let toolArguments = '';
	let sawDone = false;

	try {
		sawDone = await pumpSseStream(res.body as ReadableStream<Uint8Array>, {
			onDelta: (delta) => {
				fullText += delta;
				onText({
					fullText,
					fullReasoning,
					toolCall: toolName
						? { name: toolName, rawParams: {}, isDone: false, doneParams: [], id: toolId }
						: undefined,
				});
			},
			onReasoningDelta: (delta) => {
				fullReasoning += delta;
				onText({
					fullText,
					fullReasoning,
					toolCall: toolName
						? { name: toolName, rawParams: {}, isDone: false, doneParams: [], id: toolId }
						: undefined,
				});
			},
			onToolDelta: (tool) => {
				if (tool.index !== 0) return;
				toolName += tool.function?.name ?? '';
				toolArguments += tool.function?.arguments ?? '';
				toolId += tool.id ?? '';
				onText({
					fullText,
					fullReasoning,
					toolCall: toolName
						? { name: toolName, rawParams: {}, isDone: false, doneParams: [], id: toolId }
						: undefined,
				});
			},
			onMetadata: (meta) => {
				routeMetadata = meta;
				console.log(`[Founder OS] ${meta.tier ?? '?'}/${meta.provider ?? '?'}/${meta.model ?? '?'} cost=${meta.ddollarCost ?? '?'} D$ req=${meta.requestId ?? '?'}`);
			},
		}, controller.signal);
	} catch (error) {
		coordination?.fail();
		throw error;
	} finally {
		activeControllers.delete(controller);
		if (coordinationHeartbeat) clearInterval(coordinationHeartbeat);
	}

	if (!sawDone) {
		coordination?.fail();
		onError({ message: 'Founder OS: Stream ended before the completion marker.', fullError: null });
		return;
	}

	if (!fullText && !fullReasoning && !toolName) {
		coordination?.fail();
		onError({ message: 'Founder OS: Response from gateway was empty.', fullError: null });
		return;
	}

	const toolCall = completedToolCall(toolName, toolArguments, toolId);
	if (toolName && !toolCall) {
		coordination?.fail();
		onError({ message: 'Founder OS: The model returned an incomplete tool request.', fullError: null });
		return;
	}
	const finalText = toolCall
		? fullText
		: `${stripUntrustedFounderRouteReceipts(fullText)}${isReviewReconciliation
			? '\n\n**Second brain reconciliation** | read-only | dissent preserved | approval required'
			: ''}${nativeSkillReceipt(skill, skillToolTurnsUsed)}${founderRouteReceipt(routeMetadata, Date.now() - startedAt, escalationReason)}`;
	const finalParams: Parameters<OnFinalMessage>[0] = {
		fullText: finalText,
		fullReasoning,
	anthropicReasoning: null,
		...(toolCall ? { toolCall } : {}),
	};
	onFinalMessage(finalParams);
	coordination?.settle();

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
	const sawDone = await pumpSseStream(res.body as ReadableStream<Uint8Array>, {
		onDelta: (delta) => { fullText += delta; },
	}, controller.signal);

	if (!sawDone) {
		onError({ message: 'Founder OS: Autocomplete stream ended before the completion marker.', fullError: null });
		return;
	}
	if (!fullText) {
		onError({ message: 'Founder OS: Autocomplete response from gateway was empty.', fullError: null });
		return;
	}
	onFinalMessage({ fullText, fullReasoning: '', anthropicReasoning: null });
}
