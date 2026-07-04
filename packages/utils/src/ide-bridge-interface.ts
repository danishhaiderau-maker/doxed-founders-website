/**
 * IDE Bridge Interface — the contract that each IDE adapter implements.
 * Founder OS consumes these capabilities without assuming any specific IDE.
 * Not all IDEs will implement all capabilities — Founder OS adapts to what's available.
 */

export interface BridgeWorkspace {
  /** Unique workspace/session ID from the IDE */
  id: string;
  /** Human-readable workspace name (e.g., "Founder OS redesign") */
  title: string;
  /** Repository path or URL */
  repository?: string;
  /** Active git branch */
  branch?: string;
  /** IDE that owns this workspace */
  ideProvider: string;
  /** Last activity timestamp (ISO) */
  lastActiveAt: string;
  /** Whether the workspace has an active agent running */
  hasActiveAgent?: boolean;
  /** Number of messages in the conversation (if available) */
  messageCount?: number;
}

export interface BridgeSession {
  /** Session ID from the IDE */
  id: string;
  /** Cursor composer UUID when ideProvider is cursor (same as id for SQLite-backed sessions) */
  composerId?: string;
  /** Workspace this session belongs to */
  workspaceId?: string;
  /** Cursor workspaceStorage UUID (maps to workspaceStorage/<id>/ on disk) */
  workspaceStorageId?: string;
  /** Absolute local folder path for the workspace (used to focus Cursor on dispatch) */
  folderPath?: string;
  /** Session title (e.g., "Fix mobile layout") */
  title: string;
  /** One-line subtitle summarizing the session (e.g., "Edited 6 files, +115 -15") */
  subtitle?: string;
  /** Repository path or name the session was working in */
  repository?: string;
  /** Active git branch for the session */
  branch?: string;
  /** IDE that owns this session (e.g., 'cursor') */
  ideProvider?: string;
  /** Conversation messages (if restorable) */
  messages?: BridgeMessage[];
  /** Whether the full conversation can be restored */
  restorable: boolean;
  /** Last activity timestamp */
  lastActiveAt: string;
  /** Number of messages in the conversation (if available) */
  messageCount?: number;
  /** Lines added by the session, if reported by the IDE */
  totalLinesAdded?: number;
  /** Lines removed by the session, if reported by the IDE */
  totalLinesRemoved?: number;
  /** Number of files touched by the session, if reported by the IDE */
  filesChangedCount?: number;
  /** Whether the session is an agent project (vs. a regular chat) */
  isAgentProject?: boolean;
}

export interface BridgeMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
  model?: string;
}

export interface BridgeGitState {
  branch: string;
  clean: boolean;
  ahead: number;
  behind: number;
  modifiedFiles: string[];
  recentCommits: Array<{ hash: string; message: string; author: string; timestamp: string }>;
}

export interface BridgeTerminalState {
  active: boolean;
  cwd?: string;
  recentOutput?: string[];
}

export interface BridgeDeployment {
  service: string;
  status: 'deployed' | 'deploying' | 'failed' | 'idle';
  url?: string;
  timestamp: string;
}

export interface BridgeAgent {
  id: string;
  label: string;
  status: 'running' | 'waiting' | 'completed' | 'idle';
  task?: string;
  startedAt?: string;
}

export interface BridgeCapabilityReport {
  /** Can the IDE discover active workspaces? */
  discoverWorkspaces: boolean;
  /** Can the IDE list recent sessions/conversations? */
  listRecentSessions: boolean;
  /** Can the IDE resume a specific session? */
  resumeSession: boolean;
  /** Can the IDE receive prompts from the web? */
  sendPrompt: boolean;
  /** Can the IDE stream events back? */
  streamEvents: boolean;
  /** Can the IDE report git state? */
  getGitState: boolean;
  /** Can the IDE report terminal state? */
  getTerminal: boolean;
  /** Can the IDE report deployments? */
  getDeployments: boolean;
  /** Can the IDE report active agents? */
  getAgents: boolean;
}

export interface IDEBridge {
  /** IDE identifier (e.g., 'cursor', 'claude_code') */
  ideId: string;

  /** Report which capabilities this IDE bridge supports */
  getCapabilities(): BridgeCapabilityReport;

  /** Discover active workspaces on the desktop */
  discoverWorkspaces(): Promise<BridgeWorkspace[]>;

  /** List recent sessions/conversations */
  listRecentSessions(workspaceId?: string): Promise<BridgeSession[]>;

  /** Resume a specific session by ID */
  resumeSession(sessionId: string): Promise<boolean>;

  /** Send a prompt to the IDE */
  sendPrompt(sessionId: string, prompt: string): Promise<boolean>;

  /** Get current git state for a workspace */
  getGitState(workspaceId: string): Promise<BridgeGitState | null>;

  /** Get terminal state for a workspace */
  getTerminal(workspaceId: string): Promise<BridgeTerminalState | null>;

  /** Get recent deployments */
  getDeployments(workspaceId: string): Promise<BridgeDeployment[]>;

  /** Get active agents */
  getAgents(workspaceId: string): Promise<BridgeAgent[]>;
}

/**
 * Default capability report for IDEs that haven't implemented the full bridge yet.
 * Everything is false by default — Founder OS adapts to what's actually available.
 */
export const DEFAULT_CAPABILITIES: BridgeCapabilityReport = {
  discoverWorkspaces: false,
  listRecentSessions: false,
  resumeSession: false,
  sendPrompt: false,
  streamEvents: false,
  getGitState: false,
  getTerminal: false,
  getDeployments: false,
  getAgents: false,
};

/**
 * Cursor's current capability report (based on what's actually implemented).
 * Cursor can discover workspaces and report git/terminal/deployments via the bridge,
 * but cannot yet list/resume conversations (liveCursorAgentsAvailable = false).
 */
export const CURSOR_CAPABILITIES: BridgeCapabilityReport = {
  discoverWorkspaces: true, // via desktop bridge taskLabel
  listRecentSessions: true, // via Founder Node state.vscdb discovery
  resumeSession: true, // via workspace composer focus + dispatch
  sendPrompt: true, // via cloud dispatch
  streamEvents: true, // via SSE
  getGitState: true, // via desktop bridge
  getTerminal: true, // via desktop bridge
  getDeployments: true, // via platform integrations
  getAgents: true, // via dispatched runs
};

/**
 * Claude Code's current capability report.
 * Claude Code stores per-project session history on disk and supports resume
 * via `claude --resume <sessionId>`, so all capabilities are reported as true.
 */
export const CLAUDE_CODE_CAPABILITIES: BridgeCapabilityReport = {
  discoverWorkspaces: true,
  listRecentSessions: true,
  resumeSession: true,
  sendPrompt: true,
  streamEvents: true,
  getGitState: true,
  getTerminal: true,
  getDeployments: true,
  getAgents: true,
};

/**
 * OpenHands' current capability report.
 */
export const OPENHANDS_CAPABILITIES: BridgeCapabilityReport = {
  discoverWorkspaces: false,
  listRecentSessions: false,
  resumeSession: false,
  sendPrompt: true, // via REST dispatch
  streamEvents: false, // polling only
  getGitState: false,
  getTerminal: false,
  getDeployments: false,
  getAgents: true, // via run snapshots
};
