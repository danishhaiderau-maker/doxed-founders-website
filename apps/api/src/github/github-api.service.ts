import { BadRequestException, Injectable } from '@nestjs/common';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';

export type GitHubCommit = { sha: string; message: string; date: string };
export type GitHubPullRequest = { title: string; url: string; state: string; number: number };
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

  async listCommits(userId: string, repo: string, perPage = 10): Promise<GitHubCommit[]> {
    const token = await this.getToken(userId);
    const res = await fetch(`https://api.github.com/repos/${repo}/commits?per_page=${perPage}`, {
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
    }));
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
    }[];
    return data.map((pr) => ({
      number: pr.number,
      title: pr.title,
      url: pr.html_url,
      state: pr.state,
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

  private headers(token: string | null): Record<string, string> {
    const h: Record<string, string> = {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'DoxxedCrypto-FounderOS',
    };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }
}
