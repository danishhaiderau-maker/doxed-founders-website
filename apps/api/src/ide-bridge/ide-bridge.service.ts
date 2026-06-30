import { Injectable } from '@nestjs/common';
import { FounderEventType } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { DesktopBridgeService } from '../desktop-bridge/desktop-bridge.service';
import { FounderAgentRunService } from '../founder-agent-run/founder-agent-run.service';

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
  ) {}

  async getRecentAgents(userId: string): Promise<RecentAgentsResponse> {
    const [bridges, activeRun, cursorEvents, cursorCred, founderNode] = await Promise.all([
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
    ]);

    const now = Date.now();
    const founderNodeOnline = Boolean(
      founderNode?.lastSeenAt && now - founderNode.lastSeenAt.getTime() < 180_000,
    );
    const cursorConnected = Boolean(cursorCred);
    const desktopOnline = bridges.some(
      (b) => now - new Date(b.updatedAt).getTime() < 180_000,
    );

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
}
