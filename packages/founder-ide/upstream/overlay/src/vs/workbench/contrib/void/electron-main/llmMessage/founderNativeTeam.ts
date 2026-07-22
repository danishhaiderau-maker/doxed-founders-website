/*--------------------------------------------------------------------------------------
 *  Founder IDE bounded native Team mode.
 *--------------------------------------------------------------------------------------*/

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

export type FounderNativeAgentMode = 'focus' | 'team';

export interface FounderTeamAdviser {
	id: 'context' | 'verification';
	label: string;
	system: string;
}

const TEAM_ADVISERS: readonly FounderTeamAdviser[] = [
	{
		id: 'context',
		label: 'Context scout',
		system: 'You are the read-only Context Scout in Founder Team. Identify the smallest relevant files, contracts, prior decisions, and nearby active work. Do not propose edits and do not use tools. Return at most six concise bullets for the editing owner.',
	},
	{
		id: 'verification',
		label: 'Verification adviser',
		system: 'You are the read-only Verification Adviser in Founder Team. Identify behavioral risks, acceptance checks, and the cheapest decisive tests for this request. Do not edit files and do not use tools. Return at most six concise bullets for the editing owner.',
	},
] as const;

export function readNativeAgentMode(): FounderNativeAgentMode {
	try {
		const file = path.join(os.homedir(), '.founder-ide', 'preferences.json');
		const parsed = JSON.parse(fs.readFileSync(file, 'utf8')) as { agentMode?: unknown };
		return parsed.agentMode === 'team' ? 'team' : 'focus';
	} catch {
		return 'focus';
	}
}

export function shouldAssembleNativeTeam(
	mode: FounderNativeAgentMode,
	chatMode: string | null,
	request: string,
): boolean {
	if (mode !== 'team' || chatMode !== 'agent') return false;
	const normalized = request.replace(/\s+/g, ' ').trim();
	if (normalized.length < 24) return false;
	return /\b(?:add|audit|build|change|create|debug|design|edit|finish|fix|implement|integrate|migrate|plan|refactor|release|remove|repair|ship|test|update|verify)\b/i.test(normalized);
}

export function nativeTeamAdvisers(): readonly FounderTeamAdviser[] {
	return TEAM_ADVISERS;
}

export function latestFounderRequest(messages: Array<{ role: string; content: string }>): string {
	return [...messages]
		.reverse()
		.find((message) => message.role === 'user')
		?.content
		.replace(/<[^>]+>/g, ' ')
		.replace(/\s+/g, ' ')
		.trim()
		.slice(0, 12_000) ?? '';
}

export function formatNativeTeamAdvice(
	results: Array<{ adviser: FounderTeamAdviser; text: string }>,
): string {
	const useful = results.filter((result) => result.text.trim());
	if (useful.length === 0) return '';
	return [
		'## Founder Team adviser handoff',
		'These advisers were read-only. You are the sole editing owner; verify their claims before acting.',
		...useful.map((result) => `### ${result.adviser.label}\n${result.text.trim().slice(0, 4_000)}`),
	].join('\n');
}
