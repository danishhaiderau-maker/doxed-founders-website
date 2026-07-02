/**
 * IDE adapter layer — abstracts over Cursor, OpenHands, and future IDEs for the
 * workspace UI. The existing `BuildAdapterId` in `provider-adapters.ts` is the
 * internal dispatch identifier (cursor / openhands / founder_node / none) and is
 * kept for backward compat. This `IdeAdapterId` is a superset for UI/selection
 * purposes — it includes "coming soon" entries so the frontend can render them.
 */

export type IdeAdapterId =
  | 'cursor'
  | 'openhands'
  | 'claude_code'
  | 'windsurf'
  | 'vscode';

export interface IdeAdapter {
  id: IdeAdapterId;
  label: string;
  /** Whether this IDE is currently supported (vs "coming soon"). */
  available: boolean;
  /** Whether the IDE supports remote session resume. */
  supportsResume: boolean;
  /** Whether the IDE supports dispatching instructions from the web. */
  supportsDispatch: boolean;
  /** Whether the IDE supports live event streaming. */
  supportsEventStream: boolean;
  /** Credential/env var key needed to connect. */
  credentialKey?: string;
}

export const IDE_ADAPTERS: IdeAdapter[] = [
  {
    id: 'cursor',
    label: 'Cursor',
    available: true,
    supportsResume: true,
    supportsDispatch: true,
    supportsEventStream: true,
    credentialKey: 'cursor',
  },
  {
    id: 'openhands',
    label: 'OpenHands',
    available: true,
    supportsResume: false,
    supportsDispatch: true,
    supportsEventStream: false,
    credentialKey: 'openhands',
  },
  {
    id: 'claude_code',
    label: 'Claude Code',
    available: true,
    supportsResume: true,
    supportsDispatch: true,
    supportsEventStream: true,
  },
  {
    id: 'windsurf',
    label: 'Windsurf',
    available: false,
    supportsResume: false,
    supportsDispatch: false,
    supportsEventStream: false,
  },
  {
    id: 'vscode',
    label: 'VS Code',
    available: false,
    supportsResume: false,
    supportsDispatch: false,
    supportsEventStream: false,
  },
];

export function getIdeAdapter(id: string): IdeAdapter | undefined {
  return IDE_ADAPTERS.find((a) => a.id === id);
}

export function getAvailableIdeAdapters(): IdeAdapter[] {
  return IDE_ADAPTERS.filter((a) => a.available);
}

export function getComingSoonIdeAdapters(): IdeAdapter[] {
  return IDE_ADAPTERS.filter((a) => !a.available);
}
