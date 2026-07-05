import { Injectable, NotFoundException } from '@nestjs/common';
import { FounderEventType } from '@prisma/client';
import {
  CLAUDE_CODE_CAPABILITIES,
  CURSOR_CAPABILITIES,
  DEFAULT_CAPABILITIES,
  OPENHANDS_CAPABILITIES,
  type BridgeCapabilityReport,
  type BridgeMessage,
  type BridgeSession,
  type BridgeWorkspace,
  withFounderOsDispatchAttribution,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { DesktopBridgeService } from '../desktop-bridge/desktop-bridge.service';
import { FounderAgentRunService } from '../founder-agent-run/founder-agent-run.service';
import { BuilderService } from '../builder/builder.service';
import { ConnectedWorkspaceService } from '../connected-workspace/connected-workspace.service';

export type PendingIdeDispatchRow = {
  id: string;
  sessionId: string;
  prompt: string;
  ideProvider: string;
};

const DISPATCH_DEDUPE_MS = 60_000;
const DISPATCH_ATTACH_RE =
  /<!--founder-attach:image:[\s\S]*?-->/g;

/** Strip attachment blocks for duplicate detection — user-visible text only. */
function normalizeDispatchPromptForDedupe(prompt: string): string {
  return prompt.replace(DISPATCH_ATTACH_RE, '').replace(/\s+/g, ' ').trim();
}

export type RecentAgentMessage = {
  role: 'user' | 'assistant';
  text: string;
  at: string | null;
};

export type RecentAgent = {
  id: string;
  label: string;
  source: 'live_desktop' | 'dispatched_run' | 'cursor_history';
  status: string;
  repository: string | null;
  branch: string | null;
  agentId: string | null;
  runId: string | null;
  lastActivityAt: string | null;
  lastUserPrompt: string | null;
  lastAssistantSnippet: string | null;
  recentMessages: RecentAgentMessage[];
};

export type RecentAgentsResponse = {
  agents: RecentAgent[];
  desktopOnline: boolean;
  cursorConnected: boolean;
  founderNodeOnline: boolean;
  /** True when Founder Node is reporting real Cursor SDK Agent.list output. */
  liveCursorAgentsAvailable: boolean;
  generatedAt: string;
};

const RECENT_AGENT_LIMIT = 5;
const RECENT_MESSAGE_LIMIT = 5;
const SNIPPET_LEN = 240;
/** Match founder-node-sync ONLINE_WINDOW_MS — bridge rows can lag slightly. */
const BRIDGE_ONLINE_MS = 180_000;

function snippet(text: string | null | undefined): string | null {
  if (!text || typeof text !== 'string') return null;
  const trimmed = text.trim();
  if (!trimmed) return null;
  return trimmed.length > SNIPPET_LEN ? `${trimmed.slice(0, SNIPPET_LEN)}…` : trimmed;
}

@Injectable()
export class IdeBridgeService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly desktopBridge: DesktopBridgeService,
    private readonly agentRuns: FounderAgentRunService,
    private readonly builderService: BuilderService,
    private readonly connectedWorkspaceService: ConnectedWorkspaceService,
  ) {}

  async getRecentAgents(userId: string): Promise<RecentAgentsResponse> {
    const [bridges, activeRun, cursorEvents, cursorCred, founderNode, persistedWorkspaces, persistedSessions] =
      await Promise.all([
      this.desktopBridge.listForUser(userId),
      this.agentRuns.getActive(userId),
      this.prisma.founderEvent.findMany({
        where: { type: FounderEventType.CURSOR_BUILD_SESSION },
        orderBy: { createdAt: 'desc' },
        take: 10,
        select: { id: true, title: true, createdAt: true, payload: true },
      }),
      this.prisma.integrationCredential.findFirst({
        where: { userId, provider: 'cursor', verifiedAt: { not: null } },
        select: { id: true },
      }),
      this.prisma.founderNode.findFirst({
        where: { userId },
        orderBy: { lastSeenAt: 'desc' },
        select: { lastSeenAt: true },
      }),
      this.desktopBridge.listWorkspaces(userId),
      this.desktopBridge.listSessions(userId),
    ]);

    const now = Date.now();
    const bridgeFresh = bridges.some(
      (b) => now - new Date(b.updatedAt).getTime() < BRIDGE_ONLINE_MS,
    );
    const founderNodeDbOnline = Boolean(
      founderNode?.lastSeenAt && now - founderNode.lastSeenAt.getTime() < BRIDGE_ONLINE_MS,
    );
    const founderNodeOnline = founderNodeDbOnline || bridgeFresh;
    const cursorViaBridge =
      bridgeFresh ||
      (founderNodeDbOnline &&
        (persistedSessions.some((s) => (s.ideProvider ?? 'cursor') === 'cursor') ||
          persistedWorkspaces.some((w) => (w.ideProvider ?? 'cursor') === 'cursor')));
    const cursorConnected = Boolean(cursorCred) || cursorViaBridge;
    const desktopOnline = bridgeFresh || founderNodeDbOnline;

    const agents: RecentAgent[] = [];
    const seen = new Set<string>();

    // 1. Live desktop bridge snapshots — one per node.
    for (const bridge of bridges) {
      const id = `desktop:${bridge.nodeId}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const recentMessages: RecentAgentMessage[] = [];
      if (bridge.taskLabel) {
        recentMessages.push({
          role: 'user',
          text: bridge.taskLabel,
          at: bridge.updatedAt,
        });
      }
      if (bridge.editSummary) {
        recentMessages.push({
          role: 'assistant',
          text: bridge.editSummary,
          at: bridge.updatedAt,
        });
      }
      agents.push({
        id,
        label: bridge.label || 'Desktop Cursor',
        source: 'live_desktop',
        status: bridge.agentStatus || (desktopOnline ? 'idle' : 'offline'),
        repository: null,
        branch: bridge.branch ?? null,
        agentId: null,
        runId: null,
        lastActivityAt: bridge.updatedAt,
        lastUserPrompt: snippet(bridge.taskLabel),
        lastAssistantSnippet: snippet(bridge.editSummary),
        recentMessages: recentMessages.slice(0, RECENT_MESSAGE_LIMIT),
      });
    }

    // 2. Active dispatched run (most recent agent dispatched FROM Founder OS).
    if (activeRun && !activeRun.terminal) {
      const id = activeRun.agentId
        ? `run:${activeRun.agentId}`
        : `run:${activeRun.runId ?? activeRun.startedAt}`;
      if (!seen.has(id)) {
        seen.add(id);
        const taskText = activeRun.task || null;
        agents.push({
          id,
          label: this.labelForRun(activeRun),
          source: 'dispatched_run',
          status: activeRun.status || 'running',
          repository: activeRun.repository ?? null,
          branch: activeRun.branch ?? null,
          agentId: activeRun.agentId ?? null,
          runId: activeRun.runId ?? null,
          lastActivityAt: activeRun.updatedAt ?? activeRun.startedAt ?? null,
          lastUserPrompt: snippet(taskText),
          lastAssistantSnippet: null,
          recentMessages: taskText
            ? [{ role: 'user', text: taskText.slice(0, SNIPPET_LEN), at: activeRun.startedAt ?? null }]
            : [],
        });
      }
    }

    // 3. Cursor dispatch history (recent CURSOR_BUILD_SESSION events).
    for (const evt of cursorEvents) {
      if (agents.length >= RECENT_AGENT_LIMIT) break;
      const id = `history:${evt.id}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const payload =
        evt.payload && typeof evt.payload === 'object' && !Array.isArray(evt.payload)
          ? (evt.payload as { agentUrl?: string; mode?: string })
          : {};
      agents.push({
        id,
        label: evt.title?.slice(0, 80) || 'Cursor session',
        source: 'cursor_history',
        status: 'completed',
        repository: null,
        branch: null,
        agentId: null,
        runId: null,
        lastActivityAt: evt.createdAt.toISOString(),
        lastUserPrompt: snippet(evt.title),
        lastAssistantSnippet: payload.agentUrl ? `Agent URL: ${payload.agentUrl}` : null,
        recentMessages: evt.title
          ? [{ role: 'user', text: evt.title.slice(0, SNIPPET_LEN), at: evt.createdAt.toISOString() }]
          : [],
      });
    }

    return {
      agents: agents.slice(0, RECENT_AGENT_LIMIT),
      desktopOnline,
      cursorConnected,
      founderNodeOnline,
      // No live Agent.list output from Founder Node yet — see docs/CURSOR_REMOTE_RESUME_FEASIBILITY.md
      liveCursorAgentsAvailable: false,
      generatedAt: new Date().toISOString(),
    };
  }

  private labelForRun(run: {
    worker?: string;
    adapterLabel?: string;
    task?: string;
  }): string {
    if (run.adapterLabel) return run.adapterLabel;
    if (run.worker) return `${run.worker} agent`;
    return 'Cursor agent';
  }

  /**
   * Report which capabilities each connected IDE bridge supports.
   * Unconnected IDEs report all-false (DEFAULT_CAPABILITIES) so the frontend
   * can gracefully adapt to whatever is actually wired up.
   */
  async getCapabilities(userId: string): Promise<{
    cursor: BridgeCapabilityReport;
    openHands: BridgeCapabilityReport;
    claudeCode: BridgeCapabilityReport;
  }> {
    const connections = await this.builderService.getBuildWorkerConnections(userId);
    return {
      cursor: connections.cursor ? CURSOR_CAPABILITIES : DEFAULT_CAPABILITIES,
      openHands: connections.openHands ? OPENHANDS_CAPABILITIES : DEFAULT_CAPABILITIES,
      claudeCode: CLAUDE_CODE_CAPABILITIES,
    };
  }

  /**
   * Return real workspaces from the bridge — NOT event logs.
   *
   * Sources (in priority order):
   *   1. Live desktop bridge snapshots (Cursor via Founder Node)
   *   2. Active dispatched agent runs (Cursor/OpenHands dispatched FROM Founder OS)
   *   3. Connected workspaces from the DB (manual fallback)
   *
   * CURSOR_BUILD_SESSION event logs are intentionally excluded — those are
   * events, not workspaces. See `/ide-bridge/recent-agents` for history.
   */
  async getWorkspaces(userId: string): Promise<BridgeWorkspace[]> {
    // 0. Real workspaces[] array reported by Founder Node v0.6.0+ in heartbeats.
    //    These are live Cursor workspaces scanned on the desktop — preferred
    //    over every synthetic source below. Persisted in memoryGraph by the
    //    founder-node heartbeat handler via DesktopBridgeService.saveWorkspaces.
    const persistedWorkspaces = await this.desktopBridge.listWorkspaces(userId);
    if (persistedWorkspaces.length > 0) {
      return persistedWorkspaces;
    }

    const [bridges, activeRun, connectedWorkspaces] = await Promise.all([
      this.desktopBridge.listForUser(userId),
      this.agentRuns.getActive(userId),
      this.connectedWorkspaceService.listForUser(userId),
    ]);

    const workspaces: BridgeWorkspace[] = [];
    const seen = new Set<string>();

    // 1. Live desktop bridge snapshots — one per Founder Node.
    for (const bridge of bridges) {
      const id = `desktop:${bridge.nodeId}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const hasActiveAgent =
        Boolean(bridge.taskLabel) ||
        (typeof bridge.agentStatus === 'string' &&
          ['running', 'busy', 'working', 'active'].includes(
            bridge.agentStatus.toLowerCase(),
          ));
      workspaces.push({
        id,
        title: bridge.taskLabel || bridge.label || 'Cursor workspace',
        repository: undefined,
        branch: bridge.branch ?? undefined,
        ideProvider: 'cursor',
        lastActiveAt: bridge.updatedAt,
        hasActiveAgent,
      });
    }

    // 2. Active dispatched run — surfaced as a workspace so the UI can show it
    //    alongside live desktop sessions.
    if (activeRun && !activeRun.terminal) {
      const id = activeRun.agentId
        ? `run:${activeRun.agentId}`
        : `run:${activeRun.runId ?? activeRun.startedAt}`;
      if (!seen.has(id)) {
        seen.add(id);
        const ideProvider =
          activeRun.adapterId === 'openhands' ? 'openhands' : 'cursor';
        workspaces.push({
          id,
          title: this.labelForRun(activeRun),
          repository: activeRun.repository ?? undefined,
          branch: activeRun.branch ?? undefined,
          ideProvider,
          lastActiveAt: activeRun.updatedAt ?? activeRun.startedAt,
          hasActiveAgent: true,
        });
      }
    }

    // 3. Connected workspaces from DB — fallback for manually-tracked projects.
    for (const cw of connectedWorkspaces) {
      const id = `db:${cw.id}`;
      if (seen.has(id)) continue;
      seen.add(id);
      workspaces.push({
        id,
        title: cw.label,
        repository: cw.repository ?? undefined,
        branch: cw.branch ?? undefined,
        ideProvider: cw.ideProvider ?? 'unknown',
        lastActiveAt: cw.lastActiveAt.toISOString(),
        hasActiveAgent: false,
      });
    }

    return workspaces;
  }

  /**
   * Return real Cursor chat/agent sessions persisted by Founder Node v0.6.1+
   * from the global state.vscdb. Newest first, up to 40 entries.
   */
  async getSessions(userId: string): Promise<BridgeSession[]> {
    return this.desktopBridge.listSessions(userId);
  }

  /**
   * Return the persisted message thread for a single session, or null when
   * the session isn't known to this user / has no messages.
   *
   * Founder Node attaches the last ~30 messages to each session in the
   * heartbeat, so we can serve them straight from the persisted sessions[]
   * without an extra round trip back to the desktop.
   */
  async getSessionMessages(userId: string, sessionId: string): Promise<BridgeMessage[]> {
    const session = await this.desktopBridge.findSessionById(userId, sessionId);
    return session?.messages ?? [];
  }

  /**
   * Create a pending IDE dispatch row. The web UI calls this when a user
   * selects a Cursor chat session and sends a message — Founder Node polls
   * {@link getPendingDispatches} and types the prompt into the local Cursor.
   *
   * NOTE: accesses the `pendingIdeDispatch` model via a cast because the
   * local Prisma client is not regenerated on Windows (file lock) — the
   * generated client on the deploy host includes the model after migration.
   */
  private get dispatchModel() {
    return (this.prisma as unknown as {
      pendingIdeDispatch: {
        create(args: { data: Record<string, unknown> }): Promise<{
          id: string;
          status: string;
        }>;
        findMany(args: {
          where: Record<string, unknown>;
          orderBy?: Record<string, unknown>;
          take?: number;
          select: Record<string, true>;
        }): Promise<Array<PendingIdeDispatchRow & { status?: string; prompt?: string }>>;
        findFirst(args: {
          where: Record<string, unknown>;
          orderBy?: Record<string, unknown>;
          select: Record<string, true>;
        }): Promise<PendingIdeDispatchRow | null>;
        update(args: { where: { id: string }; data: Record<string, unknown> }): Promise<unknown>;
        updateMany(args: {
          where: Record<string, unknown>;
          data: Record<string, unknown>;
        }): Promise<{ count: number }>;
      };
    }).pendingIdeDispatch;
  }

  async createDispatch(
    userId: string,
    sessionId: string,
    prompt: string,
    ideProvider: string,
  ) {
    const trimmed = prompt?.trim();
    if (!trimmed) throw new Error('Prompt required');
    const attributed = withFounderOsDispatchAttribution(trimmed);
    const dedupeKey = normalizeDispatchPromptForDedupe(attributed);

    // One row per user action — ignore duplicate POSTs within 60s (normalized text).
    const recent = (await this.dispatchModel.findMany({
      where: {
        userId,
        sessionId,
        OR: [
          {
            createdAt: { gte: new Date(Date.now() - DISPATCH_DEDUPE_MS) },
            status: { in: ['PENDING', 'DISPATCHING'] },
          },
          {
            dispatchedAt: { gte: new Date(Date.now() - DISPATCH_DEDUPE_MS) },
            status: 'DISPATCHED',
          },
        ],
      },
      orderBy: { createdAt: 'desc' },
      take: 12,
      select: { id: true, status: true, prompt: true },
    })) as Array<{ id: string; status: string; prompt: string }>;
    const duplicate = recent.find(
      (row) => normalizeDispatchPromptForDedupe(row.prompt) === dedupeKey,
    );
    if (duplicate) {
      return { id: duplicate.id, status: duplicate.status };
    }

    return this.dispatchModel.create({
      data: {
        userId,
        sessionId,
        prompt: attributed,
        ideProvider: ideProvider || 'cursor',
        status: 'PENDING',
      },
    });
  }

  /**
   * Return up to `take` PENDING dispatches for a user, oldest first.
   * Called by Founder Node on each sync cycle.
   */
  async getPendingDispatches(userId: string, take = 10): Promise<PendingIdeDispatchRow[]> {
    return this.dispatchModel.findMany({
      where: { userId, status: 'PENDING' },
      orderBy: { createdAt: 'asc' },
      take,
      select: { id: true, sessionId: true, prompt: true, ideProvider: true },
    });
  }

  /**
   * Atomically claim a dispatch for execution. Flips PENDING → DISPATCHING
   * only when the row is still pending for this user (compare-and-swap).
   */
  async claimDispatch(userId: string, dispatchId: string): Promise<PendingIdeDispatchRow | null> {
    const existing = await this.dispatchModel.findFirst({
      where: { id: dispatchId, userId, status: 'PENDING' },
      select: { id: true, sessionId: true, prompt: true, ideProvider: true },
    });
    if (!existing) return null;
    const claimed = await this.dispatchModel.updateMany({
      where: { id: dispatchId, userId, status: 'PENDING' },
      data: { status: 'DISPATCHING' },
    });
    if (claimed.count === 0) return null;

    // Collapse duplicate PENDING rows queued for the same session + text.
    const claimedNorm = normalizeDispatchPromptForDedupe(existing.prompt);
    const siblings = (await this.dispatchModel.findMany({
      where: {
        userId,
        sessionId: existing.sessionId,
        status: 'PENDING',
      },
      select: { id: true, prompt: true },
    })) as Array<{ id: string; prompt: string }>;
    const supersededIds = siblings
      .filter(
        (row) =>
          row.id !== dispatchId &&
          normalizeDispatchPromptForDedupe(row.prompt) === claimedNorm,
      )
      .map((row) => row.id);
    if (supersededIds.length > 0) {
      await this.dispatchModel.updateMany({
        where: { id: { in: supersededIds }, userId, status: 'PENDING' },
        data: {
          status: 'DISPATCHED',
          dispatchedAt: new Date(),
          result: 'superseded (duplicate dispatch)',
        },
      });
    }

    return existing;
  }

  async markDispatched(id: string, result?: string): Promise<void> {
    await this.dispatchModel.updateMany({
      where: { id, status: { in: ['PENDING', 'DISPATCHING'] } },
      data: {
        status: 'DISPATCHED',
        dispatchedAt: new Date(),
        ...(result ? { result: result.slice(0, 4000) } : {}),
      },
    });
  }

  /** Poll delivery outcome for a dispatch the web UI just created. */
  async getDispatchStatus(userId: string, dispatchId: string) {
    const row = (await this.dispatchModel.findFirst({
      where: { id: dispatchId, userId },
      select: {
        id: true,
        status: true,
        result: true,
        dispatchedAt: true,
        createdAt: true,
        sessionId: true,
      },
    })) as {
      id: string;
      status: string;
      result: string | null;
      dispatchedAt: Date | null;
      createdAt: Date;
      sessionId: string;
    } | null;
    if (!row) throw new NotFoundException('Dispatch not found');
    return {
      id: row.id,
      status: row.status,
      result: row.result,
      dispatchedAt: row.dispatchedAt?.toISOString() ?? null,
      createdAt: row.createdAt.toISOString(),
      sessionId: row.sessionId,
      delivered: row.status === 'DISPATCHED' && !row.result?.startsWith('error:'),
      failed: row.status === 'DISPATCHED' && Boolean(row.result?.startsWith('error:')),
    };
  }
}
