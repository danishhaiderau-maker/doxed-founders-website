import { Injectable } from '@nestjs/common';
import {
  formatWorkspaceActivityForPrompt,
  reconcileCursorAgentResult,
  type WorkspaceActivity,
} from '@dcf/utils';
import { GitHubApiService } from './github-api.service';

@Injectable()
export class WorkspaceActivityService {
  constructor(private readonly github: GitHubApiService) {}

  async resolveRepo(
    userId: string,
    repository?: string | null,
  ): Promise<string | null> {
    if (repository?.trim()) return repository.trim();
    return this.github.resolveRepo(userId, null, null);
  }

  async getActivity(userId: string, repository?: string | null): Promise<WorkspaceActivity> {
    const repo = await this.resolveRepo(userId, repository);
    if (!repo) {
      return {
        repoFullName: null,
        defaultBranch: 'main',
        syncedAt: new Date().toISOString(),
        commitsLast24h: [],
        commitsLast2h: [],
        cursorBranchCommits: [],
        localWorkHint:
          'Connect GitHub (owner/repo) in Integrations so Founder OS and Cursor share the same commit stream.',
      };
    }
    return this.github.getWorkspaceActivity(userId, repo);
  }

  buildPromptContext(activity: WorkspaceActivity): string {
    return formatWorkspaceActivityForPrompt(activity);
  }

  reconcileRunResult(agentResult: string | null | undefined, activity: WorkspaceActivity): string | null {
    return reconcileCursorAgentResult(agentResult, activity);
  }
}
