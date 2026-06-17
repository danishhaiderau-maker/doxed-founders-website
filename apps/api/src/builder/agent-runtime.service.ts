import { Injectable } from '@nestjs/common';
import {
  buildAdapterLabel,
  buildCommandCenterRuntimeSteps,
  buildAdapterToWorker,
  type BuildAdapterId,
  type FounderAgentRunWorker,
  workerToBuildAdapter,
} from '@dcf/utils';
import { FounderAgentRunService } from '../founder-agent-run/founder-agent-run.service';

export type AgentRuntimeDispatchInput = {
  adapterId: BuildAdapterId;
  worker: FounderAgentRunWorker;
  status: string;
  task: string;
  repository?: string | null;
  agentId?: string | null;
  runId?: string | null;
  conversationId?: string | null;
  prUrl?: string | null;
  branch?: string | null;
};

@Injectable()
export class AgentRuntimeService {
  constructor(private readonly agentRuns: FounderAgentRunService) {}

  buildSteps(input: {
    worker: FounderAgentRunWorker;
    status: string;
    prUrl?: string | null;
    branch?: string | null;
  }) {
    return buildCommandCenterRuntimeSteps(input);
  }

  async startRun(userId: string, input: AgentRuntimeDispatchInput) {
    const adapterId = input.adapterId === 'none' ? workerToBuildAdapter(input.worker) : input.adapterId;
    const steps = this.buildSteps({
      worker: input.worker,
      status: input.status,
      prUrl: input.prUrl,
      branch: input.branch,
    });
    return this.agentRuns.start(userId, {
      worker: input.worker,
      adapterId: adapterId === 'none' ? 'cursor' : adapterId,
      adapterLabel: buildAdapterLabel(adapterId === 'none' ? 'cursor' : adapterId),
      status: input.status,
      task: input.task,
      repository: input.repository,
      agentId: input.agentId,
      runId: input.runId,
      conversationId: input.conversationId,
      prUrl: input.prUrl,
      branch: input.branch,
      steps,
    });
  }

  async refreshFromWorkerSnapshot(
    userId: string,
    input: {
      worker: FounderAgentRunWorker;
      status: string;
      prUrl?: string | null;
      branch?: string | null;
      terminal?: boolean;
    },
  ) {
    const steps = this.buildSteps(input);
    return this.agentRuns.patch(userId, {
      status: input.status,
      prUrl: input.prUrl,
      branch: input.branch,
      terminal: input.terminal,
      steps,
    });
  }

  workerFromAdapter(adapterId: BuildAdapterId): FounderAgentRunWorker | null {
    const w = buildAdapterToWorker(adapterId);
    if (w === 'CURSOR' || w === 'OPENHANDS') return w;
    return null;
  }
}
