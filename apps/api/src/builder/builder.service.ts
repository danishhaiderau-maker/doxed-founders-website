import { BadRequestException, Injectable } from '@nestjs/common';
import { AiProvider, Prisma } from '@prisma/client';
import {
  AI_PROVIDERS,
  QUICK_BUILD_AI_SYSTEM,
  QuickBuildResult,
  aiProviderConfig,
  buildCursorCloudTaskMessage,
  buildOpenHandsTaskMessage,
  githubRepoToUrl,
  isRemoteAgentProvider,
  processQuickBuild,
} from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';
import {
  CursorCredentialMeta,
  dispatchCursorCloudTask,
  verifyCursorCloudConnection,
} from './cursor-cloud.client';
import {
  OpenHandsCredentialMeta,
  dispatchOpenHandsTask,
  verifyOpenHandsConnection,
} from './openhands.client';

@Injectable()
export class BuilderService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
  ) {}

  async getSettings(userId: string) {
    const settings = await this.ensureSettings(userId);
    const connected = await this.listConnectedProviders(userId);

    const openHandsMeta = await this.getOpenHandsMeta(userId);
    const cursorMeta = await this.getCursorMeta(userId);

    return {
      defaultProvider: settings.defaultProvider,
      preferredModel: settings.preferredModel,
      autoCreateGitHubIssues: settings.autoCreateGitHubIssues,
      autoPublishOnEvent: settings.autoPublishOnEvent,
      currentGoalFocus: settings.currentGoalFocus,
      openHandsBaseUrl: openHandsMeta?.baseUrl ?? null,
      cursorAgentUrl: cursorMeta?.agentId ? `https://cursor.com/agents/${cursorMeta.agentId}` : null,
      providers: AI_PROVIDERS.map((p) => ({
        ...p,
        connected:
          p.connectMode === 'none'
            ? p.key === 'RULE_BASED'
            : connected.has(p.credentialProvider!),
      })),
      githubTokenConnected: Boolean(
        await this.prisma.gitHubConnection.findFirst({
          where: { userId, accessTokenEncrypted: { not: null } },
        }),
      ),
    };
  }

  async updateSettings(
    userId: string,
    input: {
      defaultProvider?: AiProvider;
      preferredModel?: string;
      autoCreateGitHubIssues?: boolean;
      autoPublishOnEvent?: boolean;
      currentGoalFocus?: string;
    },
  ) {
    if (input.defaultProvider) {
      const cfg = aiProviderConfig(input.defaultProvider);
      if (!cfg) throw new BadRequestException('Unknown provider');
      if (cfg.needsApiKey || cfg.connectMode === 'remote_agent') {
        const cred = cfg.credentialProvider
          ? await this.prisma.integrationCredential.findUnique({
              where: { userId_provider: { userId, provider: cfg.credentialProvider } },
            })
          : null;
        if (!cred?.token) {
          throw new BadRequestException(`Connect ${cfg.label} before setting as default`);
        }
        if (cfg.connectMode === 'remote_agent' && cfg.key === 'OPENHANDS') {
          const meta = cred.metadata as OpenHandsCredentialMeta | null;
          if (!meta?.baseUrl) {
            throw new BadRequestException('Connect OpenHands base URL before setting as default');
          }
        }
      }
    }

    const settings = await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: {
        userId,
        defaultProvider: input.defaultProvider ?? AiProvider.RULE_BASED,
        preferredModel: input.preferredModel,
        autoCreateGitHubIssues: input.autoCreateGitHubIssues ?? false,
        autoPublishOnEvent: input.autoPublishOnEvent ?? false,
        currentGoalFocus: input.currentGoalFocus,
      },
      update: {
        ...(input.defaultProvider !== undefined ? { defaultProvider: input.defaultProvider } : {}),
        ...(input.preferredModel !== undefined ? { preferredModel: input.preferredModel } : {}),
        ...(input.autoCreateGitHubIssues !== undefined
          ? { autoCreateGitHubIssues: input.autoCreateGitHubIssues }
          : {}),
        ...(input.autoPublishOnEvent !== undefined
          ? { autoPublishOnEvent: input.autoPublishOnEvent }
          : {}),
        ...(input.currentGoalFocus !== undefined ? { currentGoalFocus: input.currentGoalFocus } : {}),
      },
    });

    return this.getSettings(userId).then(() => ({
      defaultProvider: settings.defaultProvider,
      preferredModel: settings.preferredModel,
      autoCreateGitHubIssues: settings.autoCreateGitHubIssues,
    }));
  }

  async connectAiProvider(userId: string, provider: string, apiKey: string) {
    const cfg = AI_PROVIDERS.find((p) => p.credentialProvider === provider);
    if (!cfg?.needsApiKey || cfg.connectMode === 'remote_agent') {
      throw new BadRequestException('Use OpenHands or Cursor connect for remote agents');
    }

    const key = apiKey.trim();
    if (!key) throw new BadRequestException('API key required');

    const verified = await this.verifyAiKey(provider, key);
    const encrypted = this.crypto.encrypt(key);

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider } },
      create: {
        userId,
        provider,
        token: encrypted,
        metadata: { accountName: verified.accountName, model: cfg.defaultModel } as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        token: encrypted,
        metadata: { accountName: verified.accountName, model: cfg.defaultModel } as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider } },
      create: {
        userId,
        provider,
        connected: true,
        label: cfg.label,
        metadata: { accountName: verified.accountName } as Prisma.InputJsonValue,
      },
      update: { connected: true, metadata: { accountName: verified.accountName } as Prisma.InputJsonValue },
    });

    return { success: true, provider, accountName: verified.accountName };
  }

  async connectOpenHands(userId: string, baseUrl: string, apiKey: string) {
    const url = baseUrl.trim();
    const key = apiKey.trim();
    if (!url || !key) throw new BadRequestException('OpenHands base URL and API key required');

    const verified = await verifyOpenHandsConnection(url, key);
    const encrypted = this.crypto.encrypt(key);
    const normalized = url.replace(/\/+$/, '');

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: 'openhands' } },
      create: {
        userId,
        provider: 'openhands',
        token: encrypted,
        metadata: {
          baseUrl: normalized,
          accountName: verified.accountName,
          apiVersion: verified.apiVersion,
        } as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        token: encrypted,
        metadata: {
          baseUrl: normalized,
          accountName: verified.accountName,
          apiVersion: verified.apiVersion,
        } as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider: 'openhands' } },
      create: {
        userId,
        provider: 'openhands',
        connected: true,
        label: 'OpenHands',
        metadata: { baseUrl: normalized, mode: 'remote_agent' } as Prisma.InputJsonValue,
      },
      update: {
        connected: true,
        metadata: { baseUrl: normalized, mode: 'remote_agent' } as Prisma.InputJsonValue,
      },
    });

    return {
      success: true,
      accountName: verified.accountName,
      baseUrl: normalized,
      apiVersion: verified.apiVersion,
    };
  }

  async connectCursor(userId: string, apiKey: string) {
    const key = apiKey.trim();
    if (!key) throw new BadRequestException('Cursor API key required');

    const verified = await verifyCursorCloudConnection(key);
    const encrypted = this.crypto.encrypt(key);
    const existing = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'cursor' } },
    });
    const prevMeta = (existing?.metadata as CursorCredentialMeta | null) ?? {};

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: 'cursor' } },
      create: {
        userId,
        provider: 'cursor',
        token: encrypted,
        metadata: {
          accountName: verified.accountName,
          agentId: prevMeta.agentId ?? null,
          agentRepoUrl: prevMeta.agentRepoUrl ?? null,
          latestRunId: prevMeta.latestRunId ?? null,
        } as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        token: encrypted,
        metadata: {
          accountName: verified.accountName,
          agentId: prevMeta.agentId ?? null,
          agentRepoUrl: prevMeta.agentRepoUrl ?? null,
          latestRunId: prevMeta.latestRunId ?? null,
        } as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider: 'cursor' } },
      create: {
        userId,
        provider: 'cursor',
        connected: true,
        label: 'Cursor Cloud Agents',
        metadata: { accountName: verified.accountName, mode: 'remote_agent' } as Prisma.InputJsonValue,
      },
      update: {
        connected: true,
        metadata: { accountName: verified.accountName, mode: 'remote_agent' } as Prisma.InputJsonValue,
      },
    });

    return {
      success: true,
      accountName: verified.accountName,
      agentUrl: prevMeta.agentId ? `https://cursor.com/agents/${prevMeta.agentId}` : null,
    };
  }

  async dispatchCursorBuildTask(
    userId: string,
    input: { spec: string; cursorPrompt?: string; repository?: string },
  ) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'cursor' } },
    });
    if (!cred?.token) {
      throw new BadRequestException('Connect Cursor in Settings → Builder first');
    }

    const meta = (cred.metadata as CursorCredentialMeta | null) ?? {};
    const apiKey = this.crypto.decrypt(cred.token);
    if (!apiKey) throw new BadRequestException('Cursor API key invalid — reconnect');

    const taskPrompt = buildCursorCloudTaskMessage(input.spec, input.cursorPrompt);
    const repoUrl = input.repository ? githubRepoToUrl(input.repository) : null;

    const result = await dispatchCursorCloudTask({
      apiKey,
      taskPrompt,
      repository: input.repository,
      agentId: meta.agentId,
      agentRepoUrl: meta.agentRepoUrl,
    });

    await this.prisma.integrationCredential.update({
      where: { userId_provider: { userId, provider: 'cursor' } },
      data: {
        metadata: {
          ...meta,
          agentId: result.agentId,
          agentRepoUrl: repoUrl ?? meta.agentRepoUrl ?? null,
          latestRunId: result.runId,
        } as Prisma.InputJsonValue,
      },
    });

    return result;
  }

  async dispatchOpenHandsBuildTask(
    userId: string,
    input: { spec: string; cursorPrompt?: string; repository?: string },
  ) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'openhands' } },
    });
    if (!cred?.token) {
      throw new BadRequestException('Connect OpenHands in Settings → Builder first');
    }

    const meta = cred.metadata as OpenHandsCredentialMeta | null;
    if (!meta?.baseUrl) throw new BadRequestException('OpenHands base URL missing — reconnect');

    const apiKey = this.crypto.decrypt(cred.token);
    if (!apiKey) throw new BadRequestException('OpenHands API key invalid — reconnect');

    const taskPrompt = buildOpenHandsTaskMessage(input.spec, input.cursorPrompt);
    const result = await dispatchOpenHandsTask(
      {
        baseUrl: meta.baseUrl,
        apiKey,
        taskPrompt,
        repository: input.repository,
      },
      meta.apiVersion,
    );

    return result;
  }

  async isOpenHandsDefault(userId: string): Promise<boolean> {
    const settings = await this.ensureSettings(userId);
    return settings.defaultProvider === AiProvider.OPENHANDS;
  }

  async disconnectAiProvider(userId: string, provider: string) {
    await this.prisma.integrationCredential.deleteMany({ where: { userId, provider } });
    await this.prisma.connectedAppStatus.updateMany({
      where: { userId, provider },
      data: { connected: false },
    });

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const cfg = AI_PROVIDERS.find((p) => p.credentialProvider === provider);
    if (settings && cfg && settings.defaultProvider === cfg.key) {
      await this.prisma.founderBuilderSettings.update({
        where: { userId },
        data: { defaultProvider: AiProvider.RULE_BASED },
      });
    }

    return { success: true };
  }

  /** User's key → provider API. Returns null → caller uses rule-based fallback. */
  async tryAiCompletion(
    userId: string,
    system: string,
    userPrompt: string,
  ): Promise<string | null> {
    const settings = await this.ensureSettings(userId);
    if (isRemoteAgentProvider(settings.defaultProvider)) {
      return null;
    }

    const cfg = aiProviderConfig(settings.defaultProvider);
    if (!cfg?.credentialProvider || cfg.connectMode !== 'api_key') return null;

    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: cfg.credentialProvider } },
    });
    const apiKey = this.crypto.decrypt(cred?.token);
    if (!apiKey) return null;

    const model = settings.preferredModel ?? cfg.defaultModel ?? undefined;

    try {
      switch (settings.defaultProvider) {
        case AiProvider.OPENAI:
          return await this.callOpenAi(apiKey, system, userPrompt, model);
        case AiProvider.ANTHROPIC:
          return await this.callAnthropic(apiKey, system, userPrompt, model);
        case AiProvider.GEMINI:
          return await this.callGemini(apiKey, system, userPrompt, model);
        default:
          return null;
      }
    } catch {
      return null;
    }
  }

  async enhanceQuickBuild(userId: string, prompt: string, projectName?: string): Promise<QuickBuildResult> {
    const fallback = processQuickBuild(prompt, projectName);
    const aiText = await this.tryAiCompletion(
      userId,
      QUICK_BUILD_AI_SYSTEM,
      `Project: ${projectName ?? 'startup'}\nFounder request: ${prompt}`,
    );
    if (!aiText) return fallback;

    try {
      const cleaned = aiText.replace(/^```json\s*/i, '').replace(/```\s*$/i, '').trim();
      const parsed = JSON.parse(cleaned) as Partial<QuickBuildResult>;
      return {
        ideaTitle: parsed.ideaTitle ?? fallback.ideaTitle,
        spec: parsed.spec ?? fallback.spec,
        tasks: parsed.tasks?.length ? parsed.tasks : fallback.tasks,
        githubIssues: parsed.githubIssues?.length ? parsed.githubIssues : fallback.githubIssues,
        roadmapTitle: parsed.roadmapTitle ?? fallback.roadmapTitle,
        cursorPrompt: parsed.cursorPrompt ?? fallback.cursorPrompt,
        traderView: parsed.traderView ?? fallback.traderView,
      };
    } catch {
      return fallback;
    }
  }

  private async getOpenHandsMeta(userId: string): Promise<OpenHandsCredentialMeta | null> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'openhands' } },
    });
    return (cred?.metadata as OpenHandsCredentialMeta | null) ?? null;
  }

  private async getCursorMeta(userId: string): Promise<CursorCredentialMeta | null> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'cursor' } },
    });
    return (cred?.metadata as CursorCredentialMeta | null) ?? null;
  }

  private async ensureSettings(userId: string) {
    return this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId },
      update: {},
    });
  }

  private async listConnectedProviders(userId: string) {
    const creds = await this.prisma.integrationCredential.findMany({
      where: { userId, verifiedAt: { not: null } },
      select: { provider: true },
    });
    return new Set(creds.map((c) => c.provider));
  }

  private async verifyAiKey(provider: string, key: string): Promise<{ accountName: string }> {
    switch (provider) {
      case 'openai': {
        const res = await fetch('https://api.openai.com/v1/models?limit=1', {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (!res.ok) throw new BadRequestException('Invalid OpenAI API key');
        return { accountName: 'OpenAI account' };
      }
      case 'anthropic': {
        const res = await fetch('https://api.anthropic.com/v1/messages', {
          method: 'POST',
          headers: {
            'x-api-key': key,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            model: 'claude-3-5-haiku-latest',
            max_tokens: 16,
            messages: [{ role: 'user', content: 'ping' }],
          }),
        });
        if (!res.ok && res.status !== 400) throw new BadRequestException('Invalid Anthropic API key');
        return { accountName: 'Anthropic account' };
      }
      case 'gemini': {
        const res = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(key)}`,
        );
        if (!res.ok) throw new BadRequestException('Invalid Gemini API key');
        return { accountName: 'Google AI account' };
      }
      default:
        throw new BadRequestException(`Unknown AI provider: ${provider}`);
    }
  }

  private async callOpenAi(key: string, system: string, user: string, model?: string) {
    const res = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: model ?? 'gpt-4o-mini',
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
        temperature: 0.4,
      }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { choices?: { message?: { content?: string } }[] };
    return data.choices?.[0]?.message?.content ?? null;
  }

  private async callAnthropic(key: string, system: string, user: string, model?: string) {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': key,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: model ?? 'claude-3-5-haiku-latest',
        max_tokens: 2048,
        system,
        messages: [{ role: 'user', content: user }],
      }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { content?: { type: string; text?: string }[] };
    return data.content?.find((c) => c.type === 'text')?.text ?? null;
  }

  private async callGemini(key: string, system: string, user: string, model?: string) {
    const modelId = model ?? 'gemini-2.0-flash';
    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${modelId}:generateContent?key=${encodeURIComponent(key)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          systemInstruction: { parts: [{ text: system }] },
          contents: [{ role: 'user', parts: [{ text: user }] }],
        }),
      },
    );
    if (!res.ok) return null;
    const data = (await res.json()) as {
      candidates?: { content?: { parts?: { text?: string }[] } }[];
    };
    return data.candidates?.[0]?.content?.parts?.[0]?.text ?? null;
  }
}
