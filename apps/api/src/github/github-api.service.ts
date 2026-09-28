import { BadRequestException, Injectable } from '@nestjs/common';
import {
  mergeCommitsDeduped,
  filterCommitsSince,
  TWO_HOURS_MS,
  type WorkspaceActivity,
  type WorkspaceCommit,
} from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';

export type GitHubCommit = { sha: string; message: string; date: string };
export type GitHubPullRequest = {
  title: string;
  url: string;
  state: string;
  number: number;
  createdAt?: string;
};
export type GitHubIssueResult = { number: number; url: string; title: string };

@Injectable()
export class GitHubApiService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
  ) {}

  async getToken(userId: string): Promise<string | null> {
    const conn = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    return this.crypto.decrypt(conn?.accessTokenEncrypted);
  }

  async verifyAndStoreToken(userId: string, token: string) {
    const res = await fetch('https://api.github.com/user', {
      headers: this.headers(token),
    });
    if (!res.ok) throw new BadRequestException('Invalid GitHub token — needs repo scope for issues');

    const user = (await res.json()) as { login?: string };
    const encrypted = this.crypto.encrypt(token.trim());

    const conn = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    if (conn) {
      await this.prisma.gitHubConnection.update({
        where: { userId },
        data: { accessTokenEncrypted: encrypted, githubUsername: user.login ?? conn.githubUsername },
      });
    } else {
      throw new BadRequestException('Connect a GitHub repository first (owner/repo)');
    }

    return { success: true, githubUsername: user.login ?? conn.githubUsername };
  }

  async clearToken(userId: string) {
    await this.prisma.gitHubConnection.updateMany({
      where: { userId },
      data: { accessTokenEncrypted: null },
    });
    return { success: true };
  }

  hasToken(userId: string) {
    return this.getToken(userId).then(Boolean);
  }

  async resolveRepo(userId: string, founderRepo?: string | null, projectRepo?: string | null) {
    const conn = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    return conn?.repoFullName ?? founderRepo ?? projectRepo ?? null;
  }

  /** Resolves GitHub default branch (e.g. master vs main) for Cursor Cloud agent dispatch. */
  async getDefaultBranch(userId: string, repo: string): Promise<string> {
    const token = await this.getToken(userId);
    const res = await fetch(`https://api.github.com/repos/${repo}`, {
      headers: this.headers(token),
    });
    if (res.ok) {
      const data = (await res.json()) as { default_branch?: string };
      if (data.default_branch?.trim()) return data.default_branch.trim();
    }
    return process.env.CURSOR_DEFAULT_BRANCH?.trim() || 'master';
  }

  async listCommits(userId: string, repo: string, perPage = 10): Promise<GitHubCommit[]> {
    return this.listCommitsOnRef(userId, repo, perPage);
  }

  async listCommitsOnRef(
    userId: string,
    repo: string,
    perPage = 10,
    ref?: string,
    since?: Date,
  ): Promise<WorkspaceCommit[]> {
    const token = await this.getToken(userId);
    const params = new URLSearchParams({ per_page: String(perPage) });
    if (ref) params.set('sha', ref);
    if (since) params.set('since', since.toISOString());
    const res = await fetch(`https://api.github.com/repos/${repo}/commits?${params}`, {
      headers: this.headers(token),
    });
    if (!res.ok) return [];
    const data = (await res.json()) as {
      sha: string;
      commit: { message: string; author: { date: string } };
    }[];
    return data.map((c) => ({
      sha: c.sha.slice(0, 7),
      message: c.commit.message.split('\n')[0] ?? c.commit.message,
      date: c.commit.author.date,
      branch: ref,
    }));
  }

  async listBranches(userId: string, repo: string, perPage = 100): Promise<string[]> {
    const token = await this.getToken(userId);
    const res = await fetch(`https://api.github.com/repos/${repo}/branches?per_page=${perPage}`, {
      headers: this.headers(token),
    });
    if (!res.ok) return [];
    const data = (await res.json()) as { name: string }[];
    return data.map((b) => b.name);
  }

  /** Ground truth for Copilot + Cursor — all recent pushes across default and cursor/* branches. */
  async getWorkspaceActivity(userId: string, repo: string): Promise<WorkspaceActivity> {
    const defaultBranch = await this.getDefaultBranch(userId, repo);
    const since24h = new Date(Date.now() - 24 * 60 * 60 * 1000);

    const defaultCommits = await this.listCommitsOnRef(userId, repo, 30, defaultBranch, since24h);

    const branches = await this.listBranches(userId, repo);
    const cursorBranches = branches
      .filter((b) => b.startsWith('cursor/') || b.includes('cursor'))
      .slice(0, 8);

    const cursorGroups: WorkspaceCommit[][] = [];
    for (const branch of cursorBranches) {
      const commits = await this.listCommitsOnRef(userId, repo, 5, branch, since24h);
      if (commits.length > 0) cursorGroups.push(commits);
    }

    const all = mergeCommitsDeduped(defaultCommits, ...cursorGroups);
    const commitsLast2h = filterCommitsSince(all, TWO_HOURS_MS);

    return {
      repoFullName: repo,
      defaultBranch,
      syncedAt: new Date().toISOString(),
      commitsLast24h: all,
      commitsLast2h,
      cursorBranchCommits: mergeCommitsDeduped(...cursorGroups),
      localWorkHint:
        commitsLast2h.length === 0
          ? '**Tip:** Work in Cursor IDE syncs here after `git push`. Cloud Agents do not see uncommitted local files.'
          : undefined,
    };
  }

  async fetchLatestCommit(
    userId: string,
    repo: string,
  ): Promise<{ fullSha: string; shortSha: string; message: string; date: string } | null> {
    const token = await this.getToken(userId);
    const res = await fetch(`https://api.github.com/repos/${repo}/commits?per_page=1`, {
      headers: this.headers(token),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      sha: string;
      commit: { message: string; author: { date: string } };
    }[];
    const latest = data[0];
    if (!latest) return null;
    return {
      fullSha: latest.sha,
      shortSha: latest.sha.slice(0, 7),
      message: latest.commit.message.split('\n')[0] ?? latest.commit.message,
      date: latest.commit.author.date,
    };
  }

  async mergePullRequest(
    userId: string,
    repo: string,
    prNumber: number,
    mergeMethod: 'merge' | 'squash' | 'rebase' = 'squash',
  ): Promise<{ merged: boolean; message: string; sha?: string }> {
    const token = await this.getToken(userId);
    if (!token) {
      throw new BadRequestException('Connect a GitHub personal access token in Builder settings');
    }
    const res = await fetch(`https://api.github.com/repos/${repo}/pulls/${prNumber}/merge`, {
      method: 'PUT',
      headers: { ...this.headers(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ merge_method: mergeMethod }),
    });
    const body = (await res.json().catch(() => ({}))) as {
      merged?: boolean;
      message?: string;
      sha?: string;
    };
    if (!res.ok) {
      throw new BadRequestException(body.message ?? `Could not merge PR #${prNumber}`);
    }
    return {
      merged: Boolean(body.merged),
      message: body.message ?? `Merged PR #${prNumber}`,
      sha: body.sha,
    };
  }

  async listPullRequests(userId: string, repo: string): Promise<GitHubPullRequest[]> {
    const token = await this.getToken(userId);
    const res = await fetch(`https://api.github.com/repos/${repo}/pulls?state=all&per_page=15`, {
      headers: this.headers(token),
    });
    if (!res.ok) return [];
    const data = (await res.json()) as {
      number: number;
      title: string;
      html_url: string;
      state: string;
      created_at?: string;
    }[];
    return data.map((pr) => ({
      number: pr.number,
      title: pr.title,
      url: pr.html_url,
      state: pr.state,
      createdAt: pr.created_at ?? undefined,
    }));
  }

  async createIssue(
    userId: string,
    repo: string,
    title: string,
    body?: string,
  ): Promise<GitHubIssueResult> {
    const token = await this.getToken(userId);
    if (!token) {
      throw new BadRequestException('Connect a GitHub personal access token in Builder settings');
    }

    const res = await fetch(`https://api.github.com/repos/${repo}/issues`, {
      method: 'POST',
      headers: { ...this.headers(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, body: body ?? title }),
    });
    if (!res.ok) {
      const err = (await res.json().catch(() => ({}))) as { message?: string };
      throw new BadRequestException(err.message ?? 'Could not create GitHub issue');
    }

    const issue = (await res.json()) as { number: number; html_url: string; title: string };
    return { number: issue.number, url: issue.html_url, title: issue.title };
  }

  async getRepoFile(userId: string, repo: string, path: string): Promise<string | null> {
    const token = await this.getToken(userId);
    const encoded = path.split('/').map(encodeURIComponent).join('/');
    const res = await fetch(`https://api.github.com/repos/${repo}/contents/${encoded}`, {
      headers: this.headers(token),
    });
    if (res.status === 404) return null;
    if (!res.ok) return null;
    const data = (await res.json()) as { content?: string; encoding?: string };
    if (!data.content || data.encoding !== 'base64') return null;
    return Buffer.from(data.content, 'base64').toString('utf8');
  }

  private normalizeRepoFileContent(content: string): string {
    return content.replace(/\r\n/g, '\n').trimEnd();
  }

  /** Write multiple memory files in one commit when possible; skip unchanged blobs. */
  async upsertRepoFilesBatch(
    userId: string,
    repo: string,
    files: { path: string; content: string }[],
    message: string,
  ): Promise<{ updated: number; skipped: number }> {
    try {
      return await this.upsertRepoFilesBatchOnce(userId, repo, files, message);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      if (!/fast forward/i.test(msg)) throw err;
      return this.upsertRepoFilesBatchOnce(userId, repo, files, message);
    }
  }

  private async upsertRepoFilesBatchOnce(
    userId: string,
    repo: string,
    files: { path: string; content: string }[],
    message: string,
  ): Promise<{ updated: number; skipped: number }> {
    const pending: { path: string; content: string }[] = [];
    let skipped = 0;

    for (const file of files) {
      const existing = await this.getRepoFile(userId, repo, file.path);
      if (
        existing != null &&
        this.normalizeRepoFileContent(existing) === this.normalizeRepoFileContent(file.content)
      ) {
        skipped += 1;
        continue;
      }
      pending.push(file);
    }

    if (pending.length === 0) {
      return { updated: 0, skipped };
    }

    if (pending.length === 1) {
      const one = pending[0]!;
      await this.upsertRepoFile(userId, repo, one.path, one.content, message);
      return { updated: 1, skipped };
    }

    const token = await this.getToken(userId);
    if (!token) {
      throw new BadRequestException('Connect a GitHub personal access token in Builder settings to sync memory files');
    }

    const repoRes = await fetch(`https://api.github.com/repos/${repo}`, {
      headers: this.headers(token),
    });
    if (!repoRes.ok) {
      throw new BadRequestException('Could not read repository metadata from GitHub');
    }
    const repoMeta = (await repoRes.json()) as { default_branch?: string };
    const branch = repoMeta.default_branch ?? 'master';

    const refRes = await fetch(`https://api.github.com/repos/${repo}/git/ref/heads/${branch}`, {
      headers: this.headers(token),
    });
    if (!refRes.ok) {
      throw new BadRequestException('Could not read default branch ref from GitHub');
    }
    const refPayload = (await refRes.json()) as { object?: { sha?: string } };
    const baseCommitSha = refPayload.object?.sha;
    if (!baseCommitSha) {
      throw new BadRequestException('Could not resolve base commit for memory sync');
    }

    const commitRes = await fetch(`https://api.github.com/repos/${repo}/git/commits/${baseCommitSha}`, {
      headers: this.headers(token),
    });
    if (!commitRes.ok) {
      throw new BadRequestException('Could not read base commit for memory sync');
    }
    const baseCommit = (await commitRes.json()) as { tree?: { sha?: string } };
    const baseTreeSha = baseCommit.tree?.sha;
    if (!baseTreeSha) {
      throw new BadRequestException('Could not read base tree for memory sync');
    }

    const treeItems: { path: string; mode: '100644'; type: 'blob'; sha: string }[] = [];
    for (const file of pending) {
      const blobRes = await fetch(`https://api.github.com/repos/${repo}/git/blobs`, {
        method: 'POST',
        headers: { ...this.headers(token), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: file.content,
          encoding: 'utf-8',
        }),
      });
      if (!blobRes.ok) {
        const err = (await blobRes.json().catch(() => ({}))) as { message?: string };
        throw new BadRequestException(err.message ?? `Could not create blob for ${file.path}`);
      }
      const blob = (await blobRes.json()) as { sha?: string };
      if (!blob.sha) {
        throw new BadRequestException(`Could not create blob for ${file.path}`);
      }
      treeItems.push({ path: file.path, mode: '100644', type: 'blob', sha: blob.sha });
    }

    const treeRes = await fetch(`https://api.github.com/repos/${repo}/git/trees`, {
      method: 'POST',
      headers: { ...this.headers(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_tree: baseTreeSha,
        tree: treeItems,
      }),
    });
    if (!treeRes.ok) {
      const err = (await treeRes.json().catch(() => ({}))) as { message?: string };
      throw new BadRequestException(err.message ?? 'Could not create Git tree for memory sync');
    }
    const treePayload = (await treeRes.json()) as { sha?: string };
    if (!treePayload.sha) {
      throw new BadRequestException('Could not create Git tree for memory sync');
    }

    const newCommitRes = await fetch(`https://api.github.com/repos/${repo}/git/commits`, {
      method: 'POST',
      headers: { ...this.headers(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        tree: treePayload.sha,
        parents: [baseCommitSha],
      }),
    });
    if (!newCommitRes.ok) {
      const err = (await newCommitRes.json().catch(() => ({}))) as { message?: string };
      throw new BadRequestException(err.message ?? 'Could not create commit for memory sync');
    }
    const newCommit = (await newCommitRes.json()) as { sha?: string };
    if (!newCommit.sha) {
      throw new BadRequestException('Could not create commit for memory sync');
    }

    const updateRefRes = await fetch(`https://api.github.com/repos/${repo}/git/refs/heads/${branch}`, {
      method: 'PATCH',
      headers: { ...this.headers(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha: newCommit.sha, force: false }),
    });
    if (!updateRefRes.ok) {
      const err = (await updateRefRes.json().catch(() => ({}))) as { message?: string };
      throw new BadRequestException(err.message ?? 'Could not update branch ref for memory sync');
    }

    return { updated: pending.length, skipped };
  }

  async upsertRepoFile(
    userId: string,
    repo: string,
    path: string,
    content: string,
    message: string,
  ): Promise<{ created: boolean; sha?: string; skipped?: boolean }> {
    const token = await this.getToken(userId);
    if (!token) {
      throw new BadRequestException('Connect a GitHub personal access token in Builder settings to sync memory files');
    }

    const encoded = path.split('/').map(encodeURIComponent).join('/');
    const existingRes = await fetch(`https://api.github.com/repos/${repo}/contents/${encoded}`, {
      headers: this.headers(token),
    });
    let sha: string | undefined;
    let existingContent: string | null = null;
    if (existingRes.ok) {
      const existing = (await existingRes.json()) as { sha?: string; content?: string; encoding?: string };
      sha = existing.sha;
      if (existing.content && existing.encoding === 'base64') {
        existingContent = Buffer.from(existing.content, 'base64').toString('utf8');
      }
    }

    if (
      existingContent != null &&
      this.normalizeRepoFileContent(existingContent) === this.normalizeRepoFileContent(content)
    ) {
      return { created: false, sha, skipped: true };
    }

    const res = await fetch(`https://api.github.com/repos/${repo}/contents/${encoded}`, {
      method: 'PUT',
      headers: { ...this.headers(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        content: Buffer.from(content, 'utf8').toString('base64'),
        ...(sha ? { sha } : {}),
      }),
    });

    if (!res.ok) {
      const err = (await res.json().catch(() => ({}))) as { message?: string };
      throw new BadRequestException(err.message ?? `Could not write ${path} to GitHub`);
    }

    const payload = (await res.json()) as { content?: { sha?: string } };
    return { created: !sha, sha: payload.content?.sha };
  }

  async createUserRepo(
    userId: string,
    name: string,
    options?: { description?: string; isPrivate?: boolean },
  ): Promise<{ repoFullName: string; htmlUrl: string }> {
    const token = await this.getToken(userId);
    if (!token) {
      throw new BadRequestException('Connect GitHub OAuth or a personal access token first');
    }

    const conn = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const res = await fetch('https://api.github.com/user/repos', {
      method: 'POST',
      headers: { ...this.headers(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name.trim().replace(/[^a-zA-Z0-9._-]/g, '-'),
        description: options?.description?.slice(0, 280),
        private: options?.isPrivate ?? false,
        auto_init: false,
      }),
    });
    if (!res.ok) {
      const err = (await res.json().catch(() => ({}))) as { message?: string };
      throw new BadRequestException(err.message ?? 'Could not create GitHub repository');
    }
    const repo = (await res.json()) as { full_name: string; html_url: string; owner?: { login?: string } };
    const owner = repo.owner?.login ?? conn?.githubUsername ?? repo.full_name.split('/')[0]!;
    return { repoFullName: repo.full_name, htmlUrl: repo.html_url };
  }

  async listUserRepos(userId: string, perPage = 30): Promise<{ fullName: string; private: boolean }[]> {
    const token = await this.getToken(userId);
    if (!token) return [];
    const res = await fetch(`https://api.github.com/user/repos?per_page=${perPage}&sort=updated`, {
      headers: this.headers(token),
    });
    if (!res.ok) return [];
    const data = (await res.json()) as { full_name: string; private: boolean }[];
    return data.map((r) => ({ fullName: r.full_name, private: r.private }));
  }

  private headers(token: string | null): Record<string, string> {
    const h: Record<string, string> = {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'DoxxedCrypto-FounderOS',
    };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }
}
