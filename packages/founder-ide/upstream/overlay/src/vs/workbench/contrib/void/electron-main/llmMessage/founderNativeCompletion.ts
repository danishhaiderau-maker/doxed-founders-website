/*--------------------------------------------------------------------------------------
 *  Founder IDE native verify-before-show completion boundary.
 *--------------------------------------------------------------------------------------*/

import type { FounderNativeWorkMode } from './founderNativeTeam.js';

export interface FounderNativeEvidenceMessage {
	role: string;
	content: string;
	tool_call_id?: string;
	tool_calls?: Array<{
		id: string;
		type?: string;
		function: { name: string; arguments: string };
	}>;
}

export interface FounderNativeCompletionReceipt {
	version: 1;
	verdict: 'passed' | 'incomplete';
	mode: FounderNativeWorkMode;
	scope: 'read_only' | 'workspace_change';
	editedFiles: readonly string[];
	passedChecks: readonly string[];
	visualChecks: readonly string[];
	missing: readonly string[];
}

const IMPLEMENTATION_REQUEST =
	/\b(?:add|build|change|create|delete|deliver|fix|implement|install|integrate|make|modify|remove|rename|replace|ship|update|wire)\b/i;
const MUTATING_TOOL =
	/(?:^|_)(?:apply|create|delete|edit|insert|mkdir|move|patch|remove|rename|replace|rewrite|write)(?:_|$)/i;
const VERIFICATION_COMMAND =
	/\b(?:build|check|jest|lint|node\s+--test|npm\s+(?:run\s+)?test|playwright|pnpm\s+(?:run\s+)?test|pytest|test|tsc|typecheck|verify|vitest)\b/i;
const VISUAL_CHECK =
	/\b(?:playwright|screenshot|visual(?:\s|-)?(?:audit|check|test|verify|verification)|viewport|pixel)\b/i;
const UI_PATH =
	/(?:^|\/)(?:apps\/web|browser\/react|components?|pages?|views?|webview|ui)(?:\/|$)|\.(?:css|less|scss|sass|tsx|jsx|html)$/i;

export function evaluateNativeFounderCompletion(input: {
	messages: readonly FounderNativeEvidenceMessage[];
	mode: FounderNativeWorkMode;
	goal: string;
	finalAnswer: string;
	requestCompleted: boolean;
}): FounderNativeCompletionReceipt {
	const latestUser = findLatestUserIndex(input.messages);
	const turn = input.messages.slice(latestUser + 1);
	const results = toolResults(turn);
	const editedFiles: string[] = [];
	const passedChecks: string[] = [];
	const visualChecks: string[] = [];

	for (const message of turn) {
		for (const call of message.tool_calls ?? []) {
			const result = results.get(call.id);
			if (!result || toolResultFailed(result)) continue;
			const name = call.function?.name ?? '';
			const args = parseArguments(call.function?.arguments ?? '');
			if (MUTATING_TOOL.test(name)) {
				editedFiles.push(toolPath(args) ?? `tool:${name}`);
			}
			if (name === 'run_command' || name === 'run_persistent_command') {
				const command = typeof args.command === 'string' ? args.command.trim() : '';
				if (command && VERIFICATION_COMMAND.test(command) && successfulCommand(result)) {
					passedChecks.push(command);
					if (VISUAL_CHECK.test(command)) visualChecks.push(command);
				}
			}
		}
	}

	const uniqueFiles = compact(editedFiles, 80);
	const uniqueChecks = compact(passedChecks, 30);
	const uniqueVisual = compact(visualChecks, 20);
	const readOnly = input.mode === 'ask' || input.mode === 'plan';
	const missing: string[] = [];

	if (!input.requestCompleted) missing.push('provider turn did not complete');
	if (!input.finalAnswer.trim()) missing.push('final answer is empty');
	if (readOnly && uniqueFiles.length > 0) {
		missing.push(`${titleCase(input.mode)} mode changed workspace files`);
	}
	if (!readOnly && (IMPLEMENTATION_REQUEST.test(input.goal) || uniqueFiles.length > 0)) {
		if (uniqueFiles.length === 0) {
			missing.push('implementation request produced no workspace edit');
		}
		if (uniqueFiles.length > 0 && uniqueChecks.length === 0) {
			missing.push('workspace edits have no passing test, build, typecheck, or lint evidence');
		}
	}
	if (
		uniqueFiles.some((file) => UI_PATH.test(normalizePath(file)))
		&& uniqueVisual.length === 0
	) {
		missing.push('user-facing UI edits have no passing visual or screenshot evidence');
	}

	return {
		version: 1,
		verdict: missing.length === 0 ? 'passed' : 'incomplete',
		mode: input.mode,
		scope: uniqueFiles.length > 0 ? 'workspace_change' : 'read_only',
		editedFiles: uniqueFiles,
		passedChecks: uniqueChecks,
		visualChecks: uniqueVisual,
		missing,
	};
}

export function founderNativeCompletionReceipt(
	receipt: FounderNativeCompletionReceipt,
): string {
	const label = receipt.verdict === 'passed' ? 'Passed' : 'Incomplete';
	const evidence = receipt.scope === 'read_only'
		? 'read-only response'
		: `${receipt.editedFiles.length} file${receipt.editedFiles.length === 1 ? '' : 's'} | ${receipt.passedChecks.length} check${receipt.passedChecks.length === 1 ? '' : 's'}`;
	const missing = receipt.missing.length > 0
		? ` | missing: ${receipt.missing.join('; ')}`
		: '';
	return `\n\n---\n**Founder verification** | ${label} | ${evidence}${missing}`;
}

function findLatestUserIndex(messages: readonly FounderNativeEvidenceMessage[]): number {
	for (let index = messages.length - 1; index >= 0; index -= 1) {
		if (messages[index].role === 'user') return index;
	}
	return -1;
}

function toolResults(
	messages: readonly FounderNativeEvidenceMessage[],
): Map<string, string> {
	const results = new Map<string, string>();
	for (const message of messages) {
		if (message.role !== 'tool' || !message.tool_call_id) continue;
		results.set(message.tool_call_id, message.content.slice(0, 40_000));
	}
	return results;
}

function parseArguments(value: string): Record<string, unknown> {
	try {
		const parsed = JSON.parse(value) as unknown;
		return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
			? parsed as Record<string, unknown>
			: {};
	} catch {
		return {};
	}
}

function toolPath(args: Record<string, unknown>): string | null {
	for (const key of ['filePath', 'path', 'uri']) {
		const value = args[key];
		if (typeof value === 'string' && value.trim()) return normalizePath(value.trim()).slice(0, 400);
		if (value && typeof value === 'object' && !Array.isArray(value)) {
			const fsPath = (value as Record<string, unknown>).fsPath;
			if (typeof fsPath === 'string' && fsPath.trim()) return normalizePath(fsPath.trim()).slice(0, 400);
		}
	}
	return null;
}

function toolResultFailed(value: string): boolean {
	const exit = value.match(/\[exit code\s+(-?\d+)\]/i);
	if (exit && Number(exit[1]) !== 0) return true;
	return /^error:/im.test(value)
		|| /\b(?:tool_error|permission denied|timed out|failed|failure)\b/i.test(value);
}

function successfulCommand(value: string): boolean {
	return /\[exit code\s+0\]/i.test(value);
}

function compact(values: readonly string[], max: number): string[] {
	return [...new Set(values.map((value) => value.trim()).filter(Boolean))].slice(0, max);
}

function normalizePath(value: string): string {
	return value.replaceAll('\\', '/').replace(/^\.?\//, '');
}

function titleCase(value: string): string {
	return value.charAt(0).toUpperCase() + value.slice(1);
}
