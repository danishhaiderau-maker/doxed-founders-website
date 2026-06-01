import { BadRequestException, Injectable } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { PrismaService } from '../prisma/prisma.service';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';

@Injectable()
export class GitHubOAuthService {
  constructor(
    private readonly jwt: JwtService,
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
  ) {}

  private clientId() {
    const id = process.env.GITHUB_CLIENT_ID?.trim();
    if (!id) throw new BadRequestException('GitHub OAuth is not configured — set GITHUB_CLIENT_ID');
    return id;
  }

  private clientSecret() {
    const secret = process.env.GITHUB_CLIENT_SECRET?.trim();
    if (!secret) throw new BadRequestException('GitHub OAuth is not configured — set GITHUB_CLIENT_SECRET');
    return secret;
  }

  callbackUrl() {
    return (
      process.env.GITHUB_OAUTH_CALLBACK_URL?.trim() ??
      `${(process.env.API_URL ?? 'http://127.0.0.1:4000').replace(/\/$/, '')}/api/auth/github/callback`
    );
  }

  webAppUrl() {
    return (process.env.WEB_APP_URL ?? process.env.NEXTAUTH_URL ?? 'http://localhost:3000').replace(/\/$/, '');
  }

  isConfigured() {
    return Boolean(process.env.GITHUB_CLIENT_ID?.trim() && process.env.GITHUB_CLIENT_SECRET?.trim());
  }

  start(userId: string) {
    const state = this.jwt.sign({ sub: userId, purpose: 'github_oauth' }, { expiresIn: '10m' });
    const params = new URLSearchParams({
      client_id: this.clientId(),
      redirect_uri: this.callbackUrl(),
      scope: 'read:user repo',
      state,
    });
    return { url: `https://github.com/login/oauth/authorize?${params.toString()}` };
  }

  async handleCallback(code: string, state: string) {
    let userId: string;
    try {
      const payload = this.jwt.verify(state) as { sub: string; purpose?: string };
      if (payload.purpose !== 'github_oauth') throw new Error('invalid purpose');
      userId = payload.sub;
    } catch {
      throw new BadRequestException('Invalid or expired GitHub OAuth state');
    }

    const tokenRes = await fetch('https://github.com/login/oauth/access_token', {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({
        client_id: this.clientId(),
        client_secret: this.clientSecret(),
        code,
        redirect_uri: this.callbackUrl(),
      }),
    });
    if (!tokenRes.ok) throw new BadRequestException('GitHub token exchange failed');

    const tokenData = (await tokenRes.json()) as { access_token?: string; error?: string; error_description?: string };
    if (!tokenData.access_token) {
      throw new BadRequestException(tokenData.error_description ?? tokenData.error ?? 'No GitHub access token');
    }

    const userRes = await fetch('https://api.github.com/user', {
      headers: {
        Authorization: `Bearer ${tokenData.access_token}`,
        Accept: 'application/vnd.github+json',
        'User-Agent': 'DoxxedCrypto-FounderOS',
      },
    });
    if (!userRes.ok) throw new BadRequestException('Could not read GitHub profile');
    const ghUser = (await userRes.json()) as { login?: string };
    const login = ghUser.login ?? 'github-user';
    const encrypted = this.crypto.encrypt(tokenData.access_token);

    const existing = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    await this.prisma.gitHubConnection.upsert({
      where: { userId },
      create: {
        userId,
        githubUsername: login,
        repoFullName: `${login}/pending-setup`,
        accessTokenEncrypted: encrypted,
      },
      update: {
        accessTokenEncrypted: encrypted,
        githubUsername: login,
        ...(existing?.repoFullName?.endsWith('/pending-setup') ? {} : {}),
      },
    });

    return {
      redirectUrl: `${this.webAppUrl()}/founder-den?github=connected`,
      githubUsername: login,
    };
  }
}
