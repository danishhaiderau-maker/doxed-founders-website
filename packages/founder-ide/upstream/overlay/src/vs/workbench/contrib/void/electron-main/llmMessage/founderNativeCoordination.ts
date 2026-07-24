/*--------------------------------------------------------------------------------------
 *  Founder IDE native chat coordination presence.
 *
 *  The built-in chat runs in Electron's main process while the Founder extension
 *  maintains the coordination view. Both sides exchange the same small, local,
 *  secret-free presence documents under ~/.founder-ide/coordination.
 *--------------------------------------------------------------------------------------*/

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

interface CoordinationMessage {
	role: string;
	content: string;
}

interface NativeCoordinationInput {
	threadId: string;
	workspacePath: string;
	provider: string;
	messages: CoordinationMessage[];
}

interface FounderAgentPresence {
	version: 1;
	id: string;
	workspacePath: string;
	workspaceName: string;
	branch?: string;
	title: string;
	goal?: string;
	expectedOutput?: string;
	dependencies?: string[];
	provider: string;
	status: 'working' | 'waiting' | 'blocked' | 'verifying' | 'complete';
	ownedFiles: string[];
	startedAt: string;
	heartbeatAt: string;
}

export interface NativeCoordinationSession {
	context: string;
	refresh(): void;
	settle(): void;
	fail(): void;
}

const PRESENCE_TTL_MS = 3 * 60_000;
const MAX_PEERS_IN_PROMPT = 6;

export function beginNativeCoordination(input: NativeCoordinationInput): NativeCoordinationSession {
	const workspacePath = path.resolve(input.workspacePath || process.cwd());
	const now = new Date().toISOString();
	const presence: FounderAgentPresence = {
		version: 1,
		id: `native-${safeFileName(input.threadId)}`,
		workspacePath,
		workspaceName: path.basename(workspacePath) || 'Founder workspace',
		branch: readGitBranch(workspacePath),
		title: taskTitle(input.messages),
		goal: taskGoal(input.messages),
		expectedOutput: 'A verified result with concise evidence and no duplicated work.',
		dependencies: [],
		provider: input.provider,
		status: 'working',
		ownedFiles: mentionedWorkspaceFiles(input.messages, workspacePath),
		startedAt: now,
		heartbeatAt: now,
	};
	const file = path.join(coordinationRoot(), `${safeFileName(presence.id)}.json`);
	writePresence(file, presence);
	const context = coordinationPrompt(presence, readPeerPresences(presence.id));

	const update = (status: FounderAgentPresence['status']) => {
		presence.status = status;
		presence.branch = readGitBranch(workspacePath);
		presence.heartbeatAt = new Date().toISOString();
		writePresence(file, presence);
	};

	return {
		context,
		refresh: () => update('working'),
		// Keep a short waiting presence between model and tool turns. The next
		// request refreshes it; stale records disappear automatically.
		settle: () => update('waiting'),
		fail: () => update('blocked'),
	};
}

function coordinationRoot(): string {
	return path.join(os.homedir(), '.founder-ide', 'coordination');
}

function writePresence(file: string, presence: FounderAgentPresence): void {
	try {
		fs.mkdirSync(coordinationRoot(), { recursive: true });
		const temp = `${file}.${process.pid}.tmp`;
		fs.writeFileSync(temp, JSON.stringify(presence, null, 2), 'utf8');
		fs.rmSync(file, { force: true });
		fs.renameSync(temp, file);
	} catch {
		// Coordination improves work quality but must never make chat unavailable.
	}
}

function readPeerPresences(selfId: string): FounderAgentPresence[] {
	const result: FounderAgentPresence[] = [];
	try {
		for (const name of fs.readdirSync(coordinationRoot())) {
			if (!name.endsWith('.json')) continue;
			try {
				const file = path.join(coordinationRoot(), name);
				const parsed = parsePresence(JSON.parse(fs.readFileSync(file, 'utf8')));
				if (!parsed || !isFresh(parsed)) {
					fs.rmSync(file, { force: true });
					continue;
				}
				if (parsed.id !== selfId) result.push(parsed);
			} catch {
				// A peer may be replacing its file atomically.
			}
		}
	} catch {
		return [];
	}
	return result.sort((a, b) => Date.parse(b.heartbeatAt) - Date.parse(a.heartbeatAt));
}

function parsePresence(value: unknown): FounderAgentPresence | null {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
	const input = value as Partial<FounderAgentPresence>;
	if (
		input.version !== 1
		|| typeof input.id !== 'string'
		|| typeof input.workspacePath !== 'string'
		|| typeof input.workspaceName !== 'string'
		|| typeof input.title !== 'string'
		|| typeof input.provider !== 'string'
		|| !Array.isArray(input.ownedFiles)
		|| typeof input.startedAt !== 'string'
		|| typeof input.heartbeatAt !== 'string'
		|| !['working', 'waiting', 'blocked', 'verifying', 'complete'].includes(String(input.status))
	) return null;
	return {
		...input,
		version: 1,
		ownedFiles: input.ownedFiles.filter((item): item is string => typeof item === 'string').slice(0, 80),
	} as FounderAgentPresence;
}

function isFresh(presence: FounderAgentPresence): boolean {
	const heartbeat = Date.parse(presence.heartbeatAt);
	return Number.isFinite(heartbeat) && Date.now() - heartbeat <= PRESENCE_TTL_MS;
}

function coordinationPrompt(self: FounderAgentPresence, peers: FounderAgentPresence[]): string {
	const workspace = normalizedPath(self.workspacePath);
	const active = peers.filter((peer) => normalizedPath(peer.workspacePath) === workspace);
	if (active.length === 0) return '';
	const selfFiles = new Set(self.ownedFiles.map(normalizedPath));
	const lines = [
		'## Live agent coordination',
		`${active.length} other active task${active.length === 1 ? '' : 's'} share this workspace.`,
	];
	for (const peer of active.slice(0, MAX_PEERS_IN_PROMPT)) {
		const overlap = peer.ownedFiles.map(normalizedPath).filter((file) => selfFiles.has(file));
		const similar = intentSimilarity(self.title, peer.title) >= 0.34;
		const risk = overlap.length > 0
			? `COORDINATE: ${overlap.length} claimed file${overlap.length === 1 ? '' : 's'} overlap`
			: similar ? 'COORDINATE: goals substantially overlap' : '';
		const files = peer.ownedFiles.slice(0, 5).join(', ') || 'no files claimed yet';
		lines.push(`- ${peer.title} [${peer.status}; ${peer.branch ?? 'no branch'}]: ${files}${risk ? `; ${risk}` : ''}`);
	}
	lines.push('Check this shared work before acting. Coordinate overlapping goals or files, avoid duplicate work, and leave unrelated tasks undisturbed.');
	return lines.join('\n');
}

function taskTitle(messages: CoordinationMessage[]): string {
	const latest = [...messages].reverse().find((message) => message.role === 'user')?.content ?? '';
	return cleanText(latest).slice(0, 140) || 'Founder AI task';
}

function taskGoal(messages: CoordinationMessage[]): string {
	const latest = [...messages].reverse().find((message) => message.role === 'user')?.content ?? '';
	return cleanText(latest).slice(0, 4_000) || 'Complete the current Founder IDE request.';
}

function cleanText(value: string): string {
	return value.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

function mentionedWorkspaceFiles(messages: CoordinationMessage[], workspacePath: string): string[] {
	const files = new Set<string>();
	const workspace = normalizedPath(workspacePath);
	const absolutePattern = /[A-Za-z]:[\\/][^\r\n<>|"']+/g;
	const relativePattern = /(?:^|[\s`"'(])((?:[A-Za-z0-9_.-]+[\\/]){1,10}[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+)/gm;
	for (const message of messages) {
		for (const raw of message.content.match(absolutePattern) ?? []) {
			const candidate = raw.trim().replace(/[),.;:]+$/, '');
			const normalized = normalizedPath(candidate);
			if (normalized === workspace || !normalized.startsWith(`${workspace}/`)) continue;
			files.add(path.relative(workspacePath, candidate).replaceAll('\\', '/'));
		}
		for (const match of message.content.matchAll(relativePattern)) {
			const candidate = match[1]?.replaceAll('\\', '/');
			if (candidate && !candidate.startsWith('http')) files.add(candidate);
		}
		if (files.size >= 80) break;
	}
	return [...files].slice(0, 80);
}

function readGitBranch(workspacePath: string): string | undefined {
	try {
		const dotGit = path.join(workspacePath, '.git');
		let gitDir = dotGit;
		if (fs.statSync(dotGit).isFile()) {
			const target = fs.readFileSync(dotGit, 'utf8').trim().replace(/^gitdir:\s*/i, '');
			gitDir = path.resolve(workspacePath, target);
		}
		const head = fs.readFileSync(path.join(gitDir, 'HEAD'), 'utf8').trim();
		return head.startsWith('ref:') ? head.replace(/^ref:\s*/, '').replace(/^refs\/heads\//, '') : head.slice(0, 8);
	} catch {
		return undefined;
	}
}

function intentSimilarity(left: string, right: string): number {
	const tokens = (value: string) => new Set(value.toLowerCase().split(/[^a-z0-9_-]+/).filter((token) => token.length >= 3));
	const a = tokens(left);
	const b = tokens(right);
	if (a.size === 0 || b.size === 0) return 0;
	let common = 0;
	for (const token of a) if (b.has(token)) common += 1;
	return common / new Set([...a, ...b]).size;
}

function safeFileName(value: string): string {
	return value.replace(/[^a-zA-Z0-9_-]/g, '-').slice(0, 180);
}

function normalizedPath(value: string): string {
	return value.trim().replaceAll('\\', '/').replace(/\/$/, '').toLowerCase();
}
