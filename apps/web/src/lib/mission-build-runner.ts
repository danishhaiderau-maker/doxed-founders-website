import {
  applyCopilotMemoryGraphAfterBuild,
  fetchBuilderCursorRun,
  fetchBuilderOpenHandsRun,
  type FounderMemoryGraph,
} from '@/lib/api';
import {
  pollCursorRunInChat,
  pollOpenHandsRunInChat,
  type BuilderRunSnapshot,
  type OpenHandsRunSnapshot,
} from '@/lib/builder-run-live';
import { isBuilderRunFailureStatus, isBuilderRunSuccessStatus } from '@dcf/utils';

export type MissionBuildApiResult = {
  graph: FounderMemoryGraph;
  taskLabel: string;
  status: string;
  worker: string;
  message: string;
  agentUrl?: string | null;
  agentId?: string | null;
  runId?: string | null;
  conversationId?: string | null;
  mode?: 'create' | 'follow_up' | null;
};

function branchFromCursor(snap: BuilderRunSnapshot) {
  return snap.git?.branches?.[0]?.branch ?? null;
}

function prFromCursor(snap: BuilderRunSnapshot) {
  return snap.git?.branches?.[0]?.prUrl ?? null;
}

async function syncAfterCursor(task: string, snap: BuilderRunSnapshot, token: string) {
  const status = snap.status;
  if (!isBuilderRunFailureStatus(status) && !isBuilderRunSuccessStatus(status)) return null;
  const res = await applyCopilotMemoryGraphAfterBuild(
    {
      task,
      status,
      result: snap.result ?? null,
      branch: branchFromCursor(snap),
      prUrl: prFromCursor(snap),
    },
    token,
  );
  return res.graph;
}

async function syncAfterOpenHands(task: string, snap: OpenHandsRunSnapshot, token: string) {
  const status = snap.status;
  if (!isBuilderRunFailureStatus(status) && !isBuilderRunSuccessStatus(status)) return null;
  const res = await applyCopilotMemoryGraphAfterBuild(
    { task, status, result: snap.result ?? null },
    token,
  );
  return res.graph;
}

/** Poll remote builder until terminal, then patch Mission State (Sprint 7d). */
export async function pollMissionBuildUntilDone(
  accessToken: string,
  build: MissionBuildApiResult,
  onProgress?: (line: string) => void,
): Promise<FounderMemoryGraph | null> {
  const task = build.taskLabel;
  onProgress?.('Builder agent working…');

  if (build.status !== 'dispatched') {
    onProgress?.(build.message);
    return build.graph;
  }

  if (build.worker === 'CURSOR' && build.agentId && build.runId) {
    const final = await pollCursorRunInChat(
      build.agentId,
      build.runId,
      accessToken,
      fetchBuilderCursorRun,
      (snap) => onProgress?.(`Cursor: ${snap.status}`),
    );
    const graph = await syncAfterCursor(task, final as BuilderRunSnapshot, accessToken);
    onProgress?.(
      isBuilderRunSuccessStatus(final.status)
        ? 'Build finished — Mission State updated'
        : `Build ended (${final.status})`,
    );
    return graph ?? null;
  }

  if (build.worker === 'OPENHANDS' && build.conversationId) {
    const final = await pollOpenHandsRunInChat(
      build.conversationId,
      accessToken,
      fetchBuilderOpenHandsRun,
      (snap) => onProgress?.(`OpenHands: ${snap.status}`),
    );
    const graph = await syncAfterOpenHands(task, final as OpenHandsRunSnapshot, accessToken);
    onProgress?.(
      isBuilderRunSuccessStatus(final.status)
        ? 'Build finished — Mission State updated'
        : `OpenHands ended (${final.status})`,
    );
    return graph ?? null;
  }

  onProgress?.(build.message);
  return build.graph;
}
