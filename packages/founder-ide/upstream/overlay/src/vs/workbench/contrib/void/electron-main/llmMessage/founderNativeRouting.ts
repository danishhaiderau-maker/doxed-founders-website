/*--------------------------------------------------------------------------------------
 *  Founder IDE native managed-routing evidence.
 *--------------------------------------------------------------------------------------*/

export type FounderNativeEscalationReason = 'high_complexity' | 'failed_verification';

/**
 * Route receipts are application-owned evidence. Provider output is untrusted
 * and may copy an older receipt from conversation history, so remove any
 * model-authored receipt line before the native boundary appends the verified
 * metadata for the current request.
 */
export function stripUntrustedFounderRouteReceipts(value: string): string {
	const lines = value.split(/\r?\n/);
	const omitted = new Set<number>();
	for (let index = 0; index < lines.length; index += 1) {
		const normalized = lines[index]
			.replace(/^[\s#>*_-]+/, '')
			.replace(/\*\*/g, '')
			.trim();
		if (!/^Founder route\b/i.test(normalized)) continue;
		omitted.add(index);
		if (index > 0 && lines[index - 1].trim() === '---') omitted.add(index - 1);
	}
	return lines
		.filter((_line, index) => !omitted.has(index))
		.join('\n')
		.replace(/\n{3,}/g, '\n\n')
		.trimEnd();
}

export function nativeAutoEscalationReason(
	messages: ReadonlyArray<{ role: string; content: string }>,
): FounderNativeEscalationReason | null {
	const toolResults = messages.filter((message) => message.role === 'tool').slice(-4);
	if (toolResults.some((message) => failedVerification(message.content))) {
		return 'failed_verification';
	}
	const request = [...messages].reverse().find((message) => message.role === 'user')?.content
		.replace(/\s+/g, ' ').trim() ?? '';
	if (!request) return null;
	let signals = 0;
	if (request.length >= 800) signals += 1;
	if (/\b(?:architecture|authentication|authorization|concurrency|migration|payments?|permissions?|privacy|release|rollback|security|signing|transaction)\b/i.test(request)) signals += 1;
	if (/\b(?:compare|coordinate|cross-check|end-to-end|trade-?offs?|verify)\b/i.test(request)) signals += 1;
	const files = new Set(request.match(/(?:[A-Za-z0-9_.-]+[\\/]){1,8}[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+/g) ?? []);
	if (files.size >= 3) signals += 1;
	return signals >= 2 ? 'high_complexity' : null;
}

function failedVerification(value: string): boolean {
	const exit = value.toLowerCase().match(/\[exit code\s+(-?\d+)\]/);
	if (exit && Number(exit[1]) !== 0) return true;
	return /\b(?:tests?|typecheck|build|lint|verification)\b[^\n]{0,80}\b(?:failed|failure|error)\b/i.test(value)
		|| /^error:/im.test(value);
}
