/*--------------------------------------------------------------------------------------
 *  Copyright 2025 Glass Devtools, Inc. All rights reserved.
 *  Licensed under the Apache License, Version 2.0. See LICENSE.txt for more information.
 *-------------------------------------------------------------------------------------*/

import { useMemo, useState } from 'react';
import { Check, Copy, LoaderCircle, MessageCircleQuestion, Pencil, Pin, PinOff, Archive, ArchiveRestore, Trash2, X } from 'lucide-react';
import { IsRunningType, ThreadType } from '../../../chatThreadService.js';
import { IconShell1 } from '../markdown/ApplyBlockHoverButtons.js';
import { useAccessor, useChatThreadsState, useFullChatThreadsStreamState } from '../util/services.js';

const FOUNDER_THREAD_PREFERENCES_KEY = 'founder.thread-preferences.v1';
const NUM_INITIAL_THREADS = 5;

type FounderThreadPreferences = {
	names: Record<string, string>;
	pinned: string[];
	archived: string[];
};

const emptyPreferences = (): FounderThreadPreferences => ({
	names: {},
	pinned: [],
	archived: [],
});

const stringArray = (value: unknown): string[] =>
	Array.isArray(value)
		? [...new Set(value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0))]
		: [];

const loadPreferences = (): FounderThreadPreferences => {
	try {
		const parsed = JSON.parse(window.localStorage.getItem(FOUNDER_THREAD_PREFERENCES_KEY) ?? '{}') as {
			names?: unknown;
			pinned?: unknown;
			archived?: unknown;
		};
		const names = parsed.names && typeof parsed.names === 'object' && !Array.isArray(parsed.names)
			? Object.fromEntries(
				Object.entries(parsed.names)
					.filter((entry): entry is [string, string] =>
						typeof entry[1] === 'string' && entry[1].trim().length > 0)
					.map(([threadId, name]) => [threadId, name.trim().slice(0, 80)]),
			)
			: {};
		return {
			names,
			pinned: stringArray(parsed.pinned),
			archived: stringArray(parsed.archived),
		};
	} catch {
		return emptyPreferences();
	}
};

const savePreferences = (preferences: FounderThreadPreferences): void => {
	window.localStorage.setItem(
		FOUNDER_THREAD_PREFERENCES_KEY,
		JSON.stringify(preferences),
	);
};

const firstUserMessage = (thread: ThreadType): string => {
	const message = thread.messages.find((candidate) => candidate.role === 'user');
	if (!message || message.role !== 'user' || typeof message.displayContent !== 'string') {
		return 'Untitled chat';
	}
	return message.displayContent.trim() || 'Untitled chat';
};

const displayName = (
	thread: ThreadType,
	preferences: FounderThreadPreferences,
): string => preferences.names[thread.id] ?? firstUserMessage(thread);

const formatDate = (date: Date): string => {
	const now = new Date();
	const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
	const yesterday = new Date(today);
	yesterday.setDate(yesterday.getDate() - 1);
	if (date >= today) return 'Today';
	if (date >= yesterday) return 'Yesterday';
	return `${date.toLocaleString('default', { month: 'short' })} ${date.getDate()}`;
};

export const PastThreadsList = ({ className = '' }: { className?: string }) => {
	const [showAll, setShowAll] = useState(false);
	const [showArchived, setShowArchived] = useState(false);
	const [query, setQuery] = useState('');
	const [hoveredId, setHoveredId] = useState<string | null>(null);
	const [preferences, setPreferences] = useState<FounderThreadPreferences>(loadPreferences);
	const threadsState = useChatThreadsState();
	const streamState = useFullChatThreadsStreamState();
	const { allThreads } = threadsState;

	const updatePreferences = (next: FounderThreadPreferences): void => {
		savePreferences(next);
		setPreferences(next);
	};

	const runningThreadIds: Record<string, IsRunningType | undefined> = {};
	for (const threadId in streamState) {
		const isRunning = streamState[threadId]?.isRunning;
		if (isRunning) runningThreadIds[threadId] = isRunning;
	}

	const sortedThreadIds = useMemo(() => {
		const normalizedQuery = query.trim().toLocaleLowerCase();
		return Object.keys(allThreads ?? {})
			.filter((threadId) => (allThreads?.[threadId]?.messages.length ?? 0) !== 0)
			.filter((threadId) => {
				const archived = preferences.archived.includes(threadId);
				return showArchived ? archived : !archived;
			})
			.filter((threadId) => {
				if (!normalizedQuery) return true;
				const thread = allThreads?.[threadId];
				return !!thread && displayName(thread, preferences)
					.toLocaleLowerCase()
					.includes(normalizedQuery);
			})
			.sort((leftId, rightId) => {
				const leftPinned = preferences.pinned.includes(leftId);
				const rightPinned = preferences.pinned.includes(rightId);
				if (leftPinned !== rightPinned) return leftPinned ? -1 : 1;
				return new Date(allThreads?.[rightId]?.lastModified ?? 0).getTime()
					- new Date(allThreads?.[leftId]?.lastModified ?? 0).getTime();
			});
	}, [allThreads, preferences, query, showArchived]);

	if (!allThreads) {
		return <div key='error' className='p-1'>Founder could not access chat history.</div>;
	}

	const hasMoreThreads = sortedThreadIds.length > NUM_INITIAL_THREADS;
	const displayThreads = showAll
		? sortedThreadIds
		: sortedThreadIds.slice(0, NUM_INITIAL_THREADS);

	return (
		<div className={`flex flex-col mb-2 gap-2 w-full text-void-fg-3 select-none relative ${className}`}>
			<div className='flex items-center gap-1'>
				<input
					type='search'
					value={query}
					onChange={(event) => setQuery(event.target.value)}
					placeholder='Search chats'
					aria-label='Search chats'
					className='h-7 min-w-0 flex-1 rounded border border-void-border-2 bg-void-bg-1 px-2 text-xs text-void-fg-1 outline-none focus:border-void-ring'
				/>
				<button
					type='button'
					className='h-7 w-7 flex items-center justify-center rounded text-void-fg-3 hover:bg-void-bg-2 hover:text-void-fg-1'
					title={showArchived ? 'Show active chats' : 'Show archived chats'}
					aria-label={showArchived ? 'Show active chats' : 'Show archived chats'}
					onClick={() => {
						setShowArchived((current) => !current);
						setShowAll(false);
					}}
				>
					{showArchived ? <ArchiveRestore size={15} /> : <Archive size={15} />}
				</button>
			</div>

			{displayThreads.length === 0
				? <div className='px-2 py-3 text-xs text-void-fg-3'>
					{query
						? 'No chats match this search.'
						: showArchived
							? 'No archived chats.'
							: 'Start a chat and it will appear here.'}
				</div>
				: displayThreads.map((threadId) => {
					const thread = allThreads[threadId];
					if (!thread) return null;
					return (
						<PastThreadElement
							key={thread.id}
							thread={thread}
							name={displayName(thread, preferences)}
							hovered={hoveredId === thread.id}
							setHovered={setHoveredId}
							isRunning={runningThreadIds[thread.id]}
							pinned={preferences.pinned.includes(thread.id)}
							archived={preferences.archived.includes(thread.id)}
							onRename={(name) => updatePreferences({
								...preferences,
								names: { ...preferences.names, [thread.id]: name },
							})}
							onTogglePin={() => updatePreferences({
								...preferences,
								pinned: preferences.pinned.includes(thread.id)
									? preferences.pinned.filter((id) => id !== thread.id)
									: [thread.id, ...preferences.pinned],
							})}
							onToggleArchive={() => updatePreferences({
								...preferences,
								pinned: preferences.pinned.filter((id) => id !== thread.id),
								archived: preferences.archived.includes(thread.id)
									? preferences.archived.filter((id) => id !== thread.id)
									: [thread.id, ...preferences.archived],
							})}
						/>
					);
				})}

			{hasMoreThreads && (
				<button
					type='button'
					className='self-start p-1 text-xs text-void-fg-3 opacity-80 hover:opacity-100'
					onClick={() => setShowAll((current) => !current)}
				>
					{showAll ? 'Show less' : `Show ${sortedThreadIds.length - NUM_INITIAL_THREADS} more`}
				</button>
			)}
		</div>
	);
};

const DuplicateButton = ({ threadId }: { threadId: string }) => {
	const accessor = useAccessor();
	const chatThreadsService = accessor.get('IChatThreadService');
	return (
		<IconShell1
			Icon={Copy}
			className='size-[11px]'
			onClick={() => chatThreadsService.duplicateThread(threadId)}
			data-tooltip-id='void-tooltip'
			data-tooltip-place='top'
			data-tooltip-content='Duplicate chat'
		/>
	);
};

const TrashButton = ({ threadId }: { threadId: string }) => {
	const accessor = useAccessor();
	const chatThreadsService = accessor.get('IChatThreadService');
	const [confirming, setConfirming] = useState(false);
	return confirming ? (
		<div className='flex flex-nowrap gap-1'>
			<IconShell1
				Icon={X}
				className='size-[11px]'
				onClick={() => setConfirming(false)}
				data-tooltip-id='void-tooltip'
				data-tooltip-place='top'
				data-tooltip-content='Cancel'
			/>
			<IconShell1
				Icon={Check}
				className='size-[11px]'
				onClick={() => {
					chatThreadsService.deleteThread(threadId);
					setConfirming(false);
				}}
				data-tooltip-id='void-tooltip'
				data-tooltip-place='top'
				data-tooltip-content='Delete chat'
			/>
		</div>
	) : (
		<IconShell1
			Icon={Trash2}
			className='size-[11px]'
			onClick={() => setConfirming(true)}
			data-tooltip-id='void-tooltip'
			data-tooltip-place='top'
			data-tooltip-content='Delete chat'
		/>
	);
};

const PastThreadElement = ({
	thread,
	name,
	hovered,
	setHovered,
	isRunning,
	pinned,
	archived,
	onRename,
	onTogglePin,
	onToggleArchive,
}: {
	thread: ThreadType;
	name: string;
	hovered: boolean;
	setHovered: (threadId: string | null) => void;
	isRunning: IsRunningType | undefined;
	pinned: boolean;
	archived: boolean;
	onRename: (name: string) => void;
	onTogglePin: () => void;
	onToggleArchive: () => void;
}) => {
	const accessor = useAccessor();
	const chatThreadsService = accessor.get('IChatThreadService');
	const [editing, setEditing] = useState(false);
	const [draftName, setDraftName] = useState(name);
	const numMessages = thread.messages
		.filter((message) => message.role === 'assistant' || message.role === 'user')
		.length;

	const commitRename = (): void => {
		const trimmed = draftName.trim().slice(0, 80);
		if (trimmed) onRename(trimmed);
		else setDraftName(name);
		setEditing(false);
	};

	return (
		<div
			className='rounded bg-zinc-700/5 px-2 py-1 text-sm opacity-85 hover:bg-zinc-700/10 hover:opacity-100 dark:bg-zinc-300/5 dark:hover:bg-zinc-300/10'
			onMouseEnter={() => setHovered(thread.id)}
			onMouseLeave={() => setHovered(null)}
		>
			<div className='flex items-center justify-between gap-1'>
				<div className='flex min-w-0 flex-1 items-center gap-2 overflow-hidden'>
					{isRunning === 'LLM' || isRunning === 'tool' || isRunning === 'idle'
						? <LoaderCircle className='flex-shrink-0 animate-spin' size={14} />
						: isRunning === 'awaiting_user'
							? <MessageCircleQuestion className='flex-shrink-0' size={14} />
							: pinned
								? <Pin className='flex-shrink-0' size={13} />
								: null}
					{editing
						? <input
							autoFocus
							value={draftName}
							onClick={(event) => event.stopPropagation()}
							onChange={(event) => setDraftName(event.target.value)}
							onBlur={commitRename}
							onKeyDown={(event) => {
								if (event.key === 'Enter') commitRename();
								if (event.key === 'Escape') {
									setDraftName(name);
									setEditing(false);
								}
							}}
							aria-label='Rename chat'
							className='min-w-0 flex-1 rounded border border-void-ring bg-void-bg-1 px-1 text-void-fg-1 outline-none'
						/>
						: <button
							type='button'
							className='min-w-0 flex-1 truncate text-left'
							onClick={() => chatThreadsService.switchToThread(thread.id)}
							data-tooltip-id='void-tooltip'
							data-tooltip-content={`${numMessages} messages`}
							data-tooltip-place='top'
						>
							{name}
						</button>}
				</div>

				<div className='flex flex-shrink-0 items-center gap-1 opacity-70'>
					{hovered ? (
						<>
							<button
								type='button'
								className='h-5 w-5 flex items-center justify-center rounded hover:bg-void-bg-2'
								title='Rename chat'
								aria-label='Rename chat'
								onClick={() => {
									setDraftName(name);
									setEditing(true);
								}}
							>
								<Pencil size={12} />
							</button>
							<button
								type='button'
								className='h-5 w-5 flex items-center justify-center rounded hover:bg-void-bg-2'
								title={pinned ? 'Unpin chat' : 'Pin chat'}
								aria-label={pinned ? 'Unpin chat' : 'Pin chat'}
								onClick={onTogglePin}
							>
								{pinned ? <PinOff size={12} /> : <Pin size={12} />}
							</button>
							<button
								type='button'
								className='h-5 w-5 flex items-center justify-center rounded hover:bg-void-bg-2'
								title={archived ? 'Restore chat' : 'Archive chat'}
								aria-label={archived ? 'Restore chat' : 'Archive chat'}
								onClick={onToggleArchive}
							>
								{archived ? <ArchiveRestore size={12} /> : <Archive size={12} />}
							</button>
							<DuplicateButton threadId={thread.id} />
							<TrashButton threadId={thread.id} />
						</>
					) : (
						<span>{numMessages} {formatDate(new Date(thread.lastModified))}</span>
					)}
				</div>
			</div>
		</div>
	);
};
