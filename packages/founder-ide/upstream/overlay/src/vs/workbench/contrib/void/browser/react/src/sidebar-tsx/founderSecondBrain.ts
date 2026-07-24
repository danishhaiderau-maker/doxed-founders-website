/*--------------------------------------------------------------------------------------
 *  Founder IDE independent reviewer context.
 *--------------------------------------------------------------------------------------*/

type FounderReviewMessage = {
	role: string;
	content?: string;
	displayContent?: string;
};

export type FounderSecondBrainIntent = 'qa' | 'audit' | 'competition';

export const founderSecondBrainIntents: ReadonlyArray<{
	id: FounderSecondBrainIntent;
	label: string;
	instruction: string;
}> = [
	{
		id: 'qa',
		label: 'QA this result',
		instruction: 'Verify whether the delivered result actually satisfies the founder goal. Inspect the changed files and run or examine the smallest relevant checks. Prioritize reproducible defects, missing acceptance evidence, and regressions.',
	},
	{
		id: 'audit',
		label: 'Audit the approach',
		instruction: 'Challenge the architecture and implementation. Find the strongest failure case, security risk, hidden assumption, unnecessary complexity, or simpler reliable design.',
	},
	{
		id: 'competition',
		label: 'Check competitive advantage',
		instruction: 'Assess the product decision against credible current alternatives. Identify what is genuinely differentiated, what is commodity, what a competitor does better, and the smallest change that materially improves the founder advantage.',
	},
];

const MAX_SECTION_CHARS = 8_000;

export function buildFounderSecondBrainPrompt(
	messages: readonly FounderReviewMessage[],
	intent: FounderSecondBrainIntent,
	reviewerLabel: string,
): string {
	const goal = lastMessageText(messages, 'user') || 'No founder goal was captured in this thread.';
	const result = lastMessageText(messages, 'assistant') || 'No completed result was captured in this thread.';
	const review = founderSecondBrainIntents.find(candidate => candidate.id === intent)
		?? founderSecondBrainIntents[0];

	return [
		'[FOUNDER_SECOND_BRAIN_V1]',
		`Independent reviewer: ${cleanSection(reviewerLabel, 120) || 'Personal AI'}`,
		`Review goal: ${review.label}`,
		'',
		'You are the independent, read-only Second brain inside Founder IDE.',
		'Use the available read, search, source-control, test, browser, and project tools now. Do not stop after announcing that you will inspect the workspace. Do not edit files, run deployments, rotate credentials, or perform destructive actions.',
		'',
		'## Original founder goal',
		cleanSection(goal),
		'',
		'## Current delivered result',
		cleanSection(result),
		'',
		'## Review instruction',
		review.instruction,
		'',
		'## Required response',
		'1. Verdict: pass, needs correction, or insufficient evidence.',
		'2. Verified findings first, ordered by severity, with file, behavior, test, screenshot, or source evidence.',
		'3. Separate verified defects from opinion and uncertain inference.',
		'4. Recommend the smallest concrete correction and name what must be re-tested.',
		'5. For a competition check, cite primary sources and state what is genuinely differentiated.',
		'6. End with confidence, remaining risk, and the evidence you actually inspected.',
	].join('\n');
}

function lastMessageText(
	messages: readonly FounderReviewMessage[],
	role: 'user' | 'assistant',
): string {
	for (let index = messages.length - 1; index >= 0; index -= 1) {
		const message = messages[index];
		if (message?.role !== role) continue;
		const text = role === 'assistant' ? message.displayContent : message.content;
		if (typeof text === 'string' && text.trim()) return text;
	}
	return '';
}

function cleanSection(value: string, limit = MAX_SECTION_CHARS): string {
	return value
		.replace(/\b(api[_-]?key|token|authorization|secret|password)\s*[:=]\s*\S+/gi, '$1=[REDACTED]')
		.slice(0, limit)
		.trim();
}
