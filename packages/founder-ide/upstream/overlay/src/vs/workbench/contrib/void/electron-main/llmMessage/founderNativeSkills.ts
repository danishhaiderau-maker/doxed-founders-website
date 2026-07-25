/*--------------------------------------------------------------------------------------
 *  Founder IDE versioned native workflow skills.
 *--------------------------------------------------------------------------------------*/

import type { FounderNativeWorkMode } from './founderNativeTeam.js';

export type FounderSkillToolPolicy = 'none' | 'read_only' | 'editing_owner';

export interface FounderNativeSkill {
	id: string;
	version: 1;
	label: string;
	mode: FounderNativeWorkMode;
	toolPolicy: FounderSkillToolPolicy;
	maxToolTurns: number;
	evidence: readonly string[];
	output: readonly string[];
	instructions: readonly string[];
}

const SKILLS: Record<FounderNativeWorkMode, FounderNativeSkill> = {
	ask: {
		id: 'founder.concise-answer',
		version: 1,
		label: 'Concise answer',
		mode: 'ask',
		toolPolicy: 'none',
		maxToolTurns: 0,
		evidence: ['State uncertainty instead of inventing repository facts.'],
		output: ['Direct answer', 'Assumptions only when material'],
		instructions: [
			'Answer the founder question directly.',
			'Do not inspect or mutate the workspace.',
		],
	},
	plan: {
		id: 'founder.evidence-plan',
		version: 1,
		label: 'Evidence-backed plan',
		mode: 'plan',
		toolPolicy: 'read_only',
		maxToolTurns: 6,
		evidence: [
			'Inspect the smallest relevant files and current change state.',
			'Name unresolved assumptions and decisive checks.',
		],
		output: ['Goal', 'Current evidence', 'Steps', 'Risks', 'Acceptance'],
		instructions: [
			'Return an executable plan without editing files.',
			'Prefer existing project contracts over invented architecture.',
		],
	},
	build: {
		id: 'founder.verified-build',
		version: 1,
		label: 'Verified build',
		mode: 'build',
		toolPolicy: 'editing_owner',
		maxToolTurns: 16,
		evidence: [
			'Inspect before editing.',
			'Run the smallest decisive checks after editing.',
			'Review the final diff against the founder goal.',
		],
		output: ['Delivered result', 'Files changed', 'Checks', 'Remaining risk'],
		instructions: [
			'You are the single editing owner.',
			'Implement the complete requested behavior and verify it before reporting.',
		],
	},
	debug: {
		id: 'founder.root-cause-debug',
		version: 1,
		label: 'Root-cause debug',
		mode: 'debug',
		toolPolicy: 'editing_owner',
		maxToolTurns: 14,
		evidence: [
			'Reproduce or capture the failure signal before editing.',
			'Connect the correction to a proven root cause.',
			'Rerun the original failing check and a focused regression check.',
		],
		output: ['Symptom', 'Root cause', 'Correction', 'Verification', 'Residual risk'],
		instructions: [
			'Do not patch symptoms without evidence for the root cause.',
			'Keep the correction as small as the complete behavior permits.',
		],
	},
	team: {
		id: 'founder.coordinated-build',
		version: 1,
		label: 'Coordinated build',
		mode: 'team',
		toolPolicy: 'editing_owner',
		maxToolTurns: 18,
		evidence: [
			'Check nearby active work and path ownership before editing.',
			'Verify adviser claims independently.',
			'Record merge and verification decisions.',
		],
		output: ['Owner result', 'Adviser input used', 'Coordination decisions', 'Checks', 'Remaining risk'],
		instructions: [
			'Read-only advisers may help, but you remain the only editing owner.',
			'Coordinate overlapping work and leave independent work undisturbed.',
		],
	},
};

export function nativeSkillForWorkMode(
	mode: FounderNativeWorkMode,
): FounderNativeSkill {
	return SKILLS[mode];
}

export function nativeSkillSystem(skill: FounderNativeSkill): string {
	return [
		`Founder workflow skill: ${skill.id}@${skill.version} (${skill.label}).`,
		`Tool policy: ${skill.toolPolicy}. Maximum tool turns for this request: ${skill.maxToolTurns}.`,
		`Instructions: ${skill.instructions.join(' ')}`,
		`Required evidence: ${skill.evidence.join(' ')}`,
		`Required result sections: ${skill.output.join('; ')}.`,
		'If the evidence is incomplete, report the missing proof instead of claiming completion.',
	].join(' ');
}

export function nativeSkillToolTurnsUsed(
	messages: ReadonlyArray<{ role: string }>,
): number {
	let latestUserIndex = -1;
	for (let index = messages.length - 1; index >= 0; index -= 1) {
		if (messages[index]?.role === 'user') {
			latestUserIndex = index;
			break;
		}
	}
	return messages
		.slice(latestUserIndex + 1)
		.filter(message => message.role === 'tool')
		.length;
}

export function nativeSkillReceipt(
	skill: FounderNativeSkill,
	toolTurnsUsed: number,
): string {
	return `\n\n**Founder skill** | ${skill.label} | ${skill.id}@${skill.version} | ${skill.toolPolicy} | tools ${toolTurnsUsed}/${skill.maxToolTurns}`;
}
