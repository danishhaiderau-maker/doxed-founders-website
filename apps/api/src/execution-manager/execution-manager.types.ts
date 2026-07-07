/**
 * Execution Manager — kernel types.
 *
 * Establishes the ExecutionAdapter contract every execution target must
 * implement (connect / readWorkspace / applyEdits / runCommand /
 * streamOutput) per docs/PRODUCT.md §5 (Input → Decision → Output) and
 * docs/KERNEL.md §3 (kernel service #4, Execution Engine).
 *
 * Payloads are typed `unknown`. Adapters narrow with their own type guards
 * before touching the payload — no `any` crosses this boundary.
 */

/**
 * The set of execution targets the kernel can orchestrate across.
 * Maps 1:1 to the Execution Targets layer in docs/KERNEL.md §2.
 */
export type ExecutionTargetId =
  | 'cursor'
  | 'vscode'
  | 'openhands'
  | 'terminal'
  | 'git'
  | 'docker'
  | 'browser'
  | 'filesystem';

/**
 * Discriminated set of action types the kernel knows how to dispatch.
 * The Decision step in the service maps an action `type` to a target
 * when the caller hasn't pinned one explicitly.
 */
export type ExecutionActionType =
  | 'shell'
  | 'file-write'
  | 'file-read'
  | 'git-commit'
  | 'git-push'
  | 'browser-open'
  | 'http';

/**
 * A single unit of work the kernel is asked to perform. The Input leg
 * of Input → Decision → Output. Callers usually pass these in batches
 * via ExecutionManagerService.executeGraph.
 *
 * `payload` is `unknown` on purpose — each adapter knows the shape it
 * expects for the action types it handles, and narrows at the boundary.
 */
export interface ExecutionAction {
  id: string;
  type: ExecutionActionType;
  /** Explicit target override. If absent, the service decides by `type`. */
  target?: ExecutionTargetId;
  /** Adapter-specific payload; narrow at the adapter boundary. */
  payload: unknown;
  /** Working directory for actions that touch the filesystem. */
  cwd?: string;
  /** Per-action timeout in milliseconds. */
  timeoutMs?: number;
}

/**
 * The Output leg. Every action the kernel runs resolves to one of these,
 * regardless of which adapter handled it.
 */
export interface ExecutionResult {
  actionId: string;
  status: 'success' | 'failed' | 'timeout' | 'skipped';
  stdout?: string;
  stderr?: string;
  exitCode?: number;
  /** Artifact paths/URLs the adapter produced (files written, URLs opened). */
  artifacts?: string[];
  startedAt: Date;
  completedAt: Date;
}

// ---------------------------------------------------------------------------
// Workspace reading
// ---------------------------------------------------------------------------

/**
 * One node in a workspace tree listing. Adapters return these from
 * readWorkspace() so the kernel/application can render a tree without
 * caring how the target enumerates files (local fs, Cursor workspace
 * snapshot, OpenHands container ls, etc.).
 */
export interface WorkspaceNode {
  path: string;
  name: string;
  type: 'file' | 'directory';
  /** Bytes for files; undefined for directories. */
  sizeBytes?: number;
  /** ISO timestamp of last modification, if the target exposes it. */
  modifiedAt?: string;
}

// ---------------------------------------------------------------------------
// File editing
// ---------------------------------------------------------------------------

/**
 * A single edit the caller wants applied. Adapters decide how — full
 * overwrite, range replacement, structured patch. `kind` lets the
 * caller signal intent without coupling to a specific adapter.
 */
export interface FileEdit {
  path: string;
  kind: 'create' | 'overwrite' | 'append' | 'patch';
  /** Full content for create/overwrite; text to append; or patch blob. */
  content: string;
  /** Optional anchor for patch-style edits (e.g. line range, regex). */
  anchor?: string;
}

/**
 * Result of applying one FileEdit. Mirrors the per-edit outcome so the
 * caller can tell which edits in a batch landed and which failed.
 */
export interface EditOutcome {
  path: string;
  ok: boolean;
  /** Bytes written, when the adapter reports it. */
  bytesWritten?: number;
  /** Set when ok === false. */
  error?: string;
}

// ---------------------------------------------------------------------------
// Command execution
// ---------------------------------------------------------------------------

export interface RunCommandOpts {
  cwd?: string;
  env?: Record<string, string>;
  timeoutMs?: number;
  /** Stream merged stderr into stdout for runCommand output. */
  mergeStderr?: boolean;
}

export interface CommandResult {
  command: string;
  exitCode: number;
  stdout: string;
  stderr: string;
  /** Wall-clock duration the adapter spent running the command. */
  durationMs: number;
  /** 'timeout' when killed by the adapter after timeoutMs. */
  signal?: 'timeout' | NodeJS.Signals;
}

/**
 * One chunk emitted by a streaming command. Adapters that implement
 * streamOutput() yield these; the service forwards them to callers
 * (Future: Founder OS shell's live output panel).
 */
export interface StreamChunk {
  /** 'stdout' | 'stderr' | or 'done' for the terminal chunk. */
  stream: 'stdout' | 'stderr' | 'done';
  data: string;
  /** Present on the terminal 'done' chunk. */
  exitCode?: number;
}

// ---------------------------------------------------------------------------
// The adapter contract
// ---------------------------------------------------------------------------

/**
 * ExecutionAdapter — the contract every execution target implements.
 *
 * connect() / disconnect() / isConnected() let the kernel track which
 * targets are live without caring about their transport (local spawn,
 * HTTP, IPC, websocket).
 *
 * readWorkspace() / applyEdits() / runCommand() are the work surfaces.
 * streamOutput() is optional — adapters that can't stream simply omit it
 * and the service falls back to runCommand().
 *
 * This is the contract called out in the Phase 3 brief: keep it small,
 * don't overengineer, but establish the surface every future target
 * (Cursor via Founder Node IPC, VS Code, OpenHands, Git, Docker, Browser)
 * will slot into.
 */
export interface ExecutionAdapter {
  readonly target: ExecutionTargetId;
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;
  readWorkspace(path?: string): Promise<WorkspaceNode[]>;
  applyEdits(edits: FileEdit[]): Promise<EditOutcome[]>;
  runCommand(command: string, opts?: RunCommandOpts): Promise<CommandResult>;
  streamOutput?(command: string, opts?: RunCommandOpts): AsyncIterable<StreamChunk>;
}
