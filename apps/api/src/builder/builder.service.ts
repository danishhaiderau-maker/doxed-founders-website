import { BadRequestException, Injectable } from '@nestjs/common';
import { AiProvider, ControlPlaneMode, MemoryStorageMode, Prisma } from '@prisma/client';
import {
  AI_PROVIDERS,
  QUICK_BUILD_AI_SYSTEM,
  QuickBuildResult,
  aiProviderConfig,
  buildCursorCloudTaskMessage,
  buildOpenHandsTaskMessage,
  githubRepoToUrl,
  isFounderNodeAiProvider,
  isRemoteAgentProvider,
  processQuickBuild,
  resolveBuildWorker,
  type BuildWorkerKey,
} from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { FounderNodeInferenceService } from '../founder-node/founder-node-inference.service';
import { FounderNodeSyncService } from '../founder-node/founder-node-sync.service';
import { AttestationService } from '../attestation/attestation.service';
import { GitHubApiService } from '../github/github-api.service';
import { PrismaService } from '../prisma/prisma.service';
import {
  CursorCredentialMeta,
  dispatchCursorCloudTask,
  fetchCursorRun,
  isCursorRunTerminal,
  verifyCursorCloudConnection,
} from './cursor-cloud.client';
import {
  OpenHandsCredentialMeta,
  dispatchOpenHandsTask,
  fetchOpenHandsConversationSnapshot,
  isOpenHandsRunTerminal,
  verifyOpenHandsConnection,
} from './openhands.client';
import {
  DEFAULT_PHALA_INFERENCE_URL,
  DEFAULT_PHALA_MODEL,
  type PhalaCredentialMeta,
  callPhalaChat,
  normalizePhalaBaseUrl,
  verifyPhalaConnection,
  type PhalaChatResult,
} from './phala.client';
import {
  estimateLlmTokensFromText,
  parseAnthropicUsage,
  parseOpenAiStyleUsage,
} from '@dcf/utils';
import { PlatformAdoptionService } from '../projects/platform-adoption.service';

type LlmUsage = { promptTokens: number; completionTokens: number };

@Injectable()
export class BuilderService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
    private readonly github: GitHubApiService,
    private readonly founderNodeInference: FounderNodeInferenceService,
    private readonly founderNodeSync: FounderNodeSyncService,
    private readonly attestation: AttestationService,
    private readonly adoption: PlatformAdoptionService,
  ) {}

  async getSettings(userId: string) {
    const settings = await this.ensureSettings(userId);
    const connected = await this.listConnectedProviders(userId);

    const openHandsMeta = await this.getOpenHandsMeta(userId);
    const cursorMeta = await this.getCursorMeta(userId);
    const ollamaStatus = await this.founderNodeInference.getOllamaStatus(userId);
    const phalaStatus = await this.getPhalaPrivateAiStatus(userId);
    const founderNodeV2 = await this.founderNodeSync.getV2Status(userId);
    const githubConn = await this.prisma.gitHubConnection.findUnique({
      where: { userId },
      select: { repoFullName: true, accessTokenEncrypted: true },
    });
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      select: { githubRepoFullName: true },
    });
    const repoFullName =
      githubConn?.repoFullName ?? founder?.githubRepoFullName ?? null;

    return {
      defaultProvider: settings.defaultProvider,
      preferredModel: settings.preferredModel,
      autoCreateGitHubIssues: settings.autoCreateGitHubIssues,
      autoPublishOnEvent: settings.autoPublishOnEvent,
      autopilotEnabled: settings.autopilotEnabled,
      autopilotRedeployHosts: settings.autopilotRedeployHosts,
      controlPlaneMode: settings.controlPlaneMode,
      currentGoalFocus: settings.currentGoalFocus,
      memoryStorageMode: settings.memoryStorageMode,
      openHandsBaseUrl: openHandsMeta?.baseUrl ?? null,
      cursorAgentUrl: cursorMeta?.agentId ? `https://cursor.com/agents/${cursorMeta.agentId}` : null,
      founderNodeAi: ollamaStatus,
      founderNodeV2,
      phalaPrivateAi: phalaStatus,
      providers: AI_PROVIDERS.map((p) => ({
        ...p,
        connected:
          p.connectMode === 'none'
            ? p.key === 'RULE_BASED'
            : p.key === 'PHALA'
              ? phalaStatus.ready
              : p.connectMode === 'founder_node'
                ? ollamaStatus.ollamaReady
                : connected.has(p.credentialProvider!),
      })),
      githubTokenConnected: Boolean(githubConn?.accessTokenEncrypted),
      repoFullName:
        repoFullName && !String(repoFullName).endsWith('/pending-setup') ? repoFullName : null,
    };
  }

  async updateSettings(
    userId: string,
    input: {
      defaultProvider?: AiProvider;
      preferredModel?: string;
      autoCreateGitHubIssues?: boolean;
      autoPublishOnEvent?: boolean;
      autopilotEnabled?: boolean;
      autopilotRedeployHosts?: boolean;
      controlPlaneMode?: ControlPlaneMode;
      currentGoalFocus?: string;
      memoryStorageMode?: 'PLATFORM' | 'GITHUB' | 'LOCAL_DEVICE' | 'LOCAL_SYNC' | 'FOUNDER_NODE';
    },
  ) {
    if (input.defaultProvider) {
      const cfg = aiProviderConfig(input.defaultProvider);
      if (!cfg) throw new BadRequestException('Unknown provider');
      if (cfg.connectMode === 'founder_node') {
        const ready = await this.founderNodeInference.isOllamaReady(userId);
        if (!ready) {
          throw new BadRequestException(
            'Pair Founder Node with Ollama running locally, or connect a direct Ollama URL first',
          );
        }
      } else if (input.defaultProvider === AiProvider.PHALA) {
        const phala = await this.resolvePhalaCredentials(userId);
        if (!phala) {
          throw new BadRequestException(
            'Connect Phala Private AI or enable platform Phala credits before setting as default',
          );
        }
      } else if (cfg.needsApiKey || cfg.connectMode === 'remote_agent') {
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
        autopilotEnabled: input.autopilotEnabled ?? false,
        autopilotRedeployHosts: input.autopilotRedeployHosts ?? false,
        controlPlaneMode: input.controlPlaneMode ?? ControlPlaneMode.FULL_STACK,
        currentGoalFocus: input.currentGoalFocus,
        memoryStorageMode: input.memoryStorageMode ?? MemoryStorageMode.PLATFORM,
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
        ...(input.autopilotEnabled !== undefined
          ? { autopilotEnabled: input.autopilotEnabled }
          : {}),
        ...(input.autopilotRedeployHosts !== undefined
          ? { autopilotRedeployHosts: input.autopilotRedeployHosts }
          : {}),
        ...(input.controlPlaneMode !== undefined
          ? { controlPlaneMode: input.controlPlaneMode }
          : {}),
        ...(input.currentGoalFocus !== undefined ? { currentGoalFocus: input.currentGoalFocus } : {}),
        ...(input.memoryStorageMode !== undefined
          ? { memoryStorageMode: input.memoryStorageMode as MemoryStorageMode }
          : {}),
      },
    });

    if (input.currentGoalFocus !== undefined) {
      await this.founderNodeSync.maybeEnqueueGoalPush(userId, input.currentGoalFocus);
    }

    return this.getSettings(userId).then(() => ({
      defaultProvider: settings.defaultProvider,
      preferredModel: settings.preferredModel,
      autoCreateGitHubIssues: settings.autoCreateGitHubIssues,
    }));
  }

  async connectAiProvider(userId: string, provider: string, apiKey: string) {
    const cfg = AI_PROVIDERS.find((p) => p.credentialProvider === provider);
    if (
      !cfg?.needsApiKey ||
      cfg.connectMode === 'remote_agent' ||
      cfg.connectMode === 'founder_node' ||
      provider === 'phala'
    ) {
      throw new BadRequestException('Use OpenHands, Cursor, Ollama, or Phala connect for this provider');
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

  async connectOllama(userId: string, baseUrl: string, model?: string) {
    const url = baseUrl.trim().replace(/\/+$/, '');
    if (!url) throw new BadRequestException('Ollama base URL required');

    await this.verifyOllamaUrl(url);

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: 'ollama' } },
      create: {
        userId,
        provider: 'ollama',
        token: this.crypto.encrypt('local'),
        metadata: {
          accountName: 'Ollama (direct URL)',
          baseUrl: url,
          model: model?.trim() || 'llama3.2',
        } as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        metadata: {
          accountName: 'Ollama (direct URL)',
          baseUrl: url,
          model: model?.trim() || 'llama3.2',
        } as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider: 'ollama' } },
      create: {
        userId,
        provider: 'ollama',
        connected: true,
        label: 'Ollama (local)',
        metadata: { baseUrl: url } as Prisma.InputJsonValue,
      },
      update: { connected: true, metadata: { baseUrl: url } as Prisma.InputJsonValue },
    });

    return { success: true, accountName: 'Ollama (direct URL)', baseUrl: url };
  }

  async connectPhala(
    userId: string,
    apiKey: string,
    inferenceUrl?: string,
    model?: string,
  ) {
    const key = apiKey.trim();
    if (!key) throw new BadRequestException('Phala API key required');

    const normalizedUrl = normalizePhalaBaseUrl(
      inferenceUrl?.trim() || process.env.PHALA_INFERENCE_URL || DEFAULT_PHALA_INFERENCE_URL,
    );
    const resolvedModel = model?.trim() || process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL;

    const verified = await verifyPhalaConnection({ apiKey: key, inferenceUrl: normalizedUrl });
    if (!verified.ok) throw new BadRequestException(verified.reason);

    const encrypted = this.crypto.encrypt(key);
    const metadata: PhalaCredentialMeta = {
      accountName: 'Phala Private AI',
      inferenceUrl: normalizedUrl,
      model: resolvedModel,
    };

    await this.prisma.integrationCredential.upsert({
      where: { userId_provider: { userId, provider: 'phala' } },
      create: {
        userId,
        provider: 'phala',
        token: encrypted,
        metadata: metadata as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
      update: {
        token: encrypted,
        metadata: metadata as Prisma.InputJsonValue,
        verifiedAt: new Date(),
      },
    });

    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider: 'phala' } },
      create: {
        userId,
        provider: 'phala',
        connected: true,
        label: 'Private AI (Phala)',
        metadata: { inferenceUrl: normalizedUrl, model: resolvedModel } as Prisma.InputJsonValue,
      },
      update: {
        connected: true,
        metadata: { inferenceUrl: normalizedUrl, model: resolvedModel } as Prisma.InputJsonValue,
      },
    });

    return { success: true, accountName: metadata.accountName, inferenceUrl: normalizedUrl, model: resolvedModel };
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
      throw new BadRequestException('Connect Cursor in AI Stack first');
    }

    const meta = (cred.metadata as CursorCredentialMeta | null) ?? {};
    const apiKey = this.crypto.decrypt(cred.token);
    if (!apiKey) throw new BadRequestException('Cursor API key invalid — reconnect');

    const taskPrompt = buildCursorCloudTaskMessage(input.spec, input.cursorPrompt);
    const repoUrl = input.repository ? githubRepoToUrl(input.repository) : null;
    const startingRef = input.repository
      ? await this.github.getDefaultBranch(userId, input.repository)
      : undefined;

    const result = await dispatchCursorCloudTask({
      apiKey,
      taskPrompt,
      repository: input.repository,
      startingRef,
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

  async getBuildWorkerConnections(userId: string) {
    const connected = await this.listConnectedProviders(userId);
    const settings = await this.ensureSettings(userId);
    const ollamaReady = await this.founderNodeInference.isOllamaReady(userId);
    return {
      cursor: connected.has('cursor'),
      openHands: connected.has('openhands'),
      founderNode: ollamaReady,
      defaultProvider: settings.defaultProvider,
    };
  }

  async resolveBuildWorker(userId: string): Promise<BuildWorkerKey> {
    const connections = await this.getBuildWorkerConnections(userId);
    return resolveBuildWorker(connections);
  }

  async getOpenHandsRunSnapshot(userId: string, conversationId: string) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'openhands' } },
    });
    if (!cred?.token) {
      throw new BadRequestException('Connect OpenHands in AI Stack first');
    }
    const meta = cred.metadata as OpenHandsCredentialMeta | null;
    if (!meta?.baseUrl) throw new BadRequestException('OpenHands base URL missing — reconnect');
    const apiKey = this.crypto.decrypt(cred.token);
    if (!apiKey) throw new BadRequestException('OpenHands API key invalid — reconnect');
    const snap = await fetchOpenHandsConversationSnapshot(
      meta.baseUrl,
      apiKey,
      conversationId,
      meta.apiVersion ?? 'v1',
    );
    return {
      ...snap,
      terminal: snap.terminal ?? isOpenHandsRunTerminal(snap.status),
    };
  }

  async getCursorRunSnapshot(userId: string, agentId: string, runId: string) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'cursor' } },
    });
    if (!cred?.token) {
      throw new BadRequestException('Connect Cursor in AI Stack first');
    }
    const apiKey = this.crypto.decrypt(cred.token);
    if (!apiKey) throw new BadRequestException('Cursor API key invalid — reconnect');
    const run = await fetchCursorRun(apiKey, agentId, runId);
    return {
      ...run,
      terminal: isCursorRunTerminal(run.status),
      agentUrl: `https://cursor.com/agents/${agentId}`,
    };
  }

  async executeBuildTask(
    userId: string,
    input: {
      spec: string;
      cursorPrompt?: string;
      repository?: string;
      worker?: 'CURSOR' | 'OPENHANDS';
    },
  ) {
    const connections = await this.getBuildWorkerConnections(userId);
    const worker =
      input.worker === 'CURSOR' && connections.cursor
        ? 'CURSOR'
        : input.worker === 'OPENHANDS' && connections.openHands
          ? 'OPENHANDS'
          : await this.resolveBuildWorker(userId);

    if (worker === 'CURSOR') {
      try {
        const result = await this.dispatchCursorBuildTask(userId, input);
        return {
          worker,
          status: 'dispatched' as const,
          agentUrl: result.agentUrl,
          agentId: result.agentId,
          runId: result.runId,
          mode: result.mode,
          cursorCloud: result,
        };
      } catch (err) {
        return {
          worker,
          status: 'error' as const,
          error: err instanceof Error ? err.message : 'Builder dispatch failed',
        };
      }
    }

    if (worker === 'OPENHANDS') {
      try {
        const result = await this.dispatchOpenHandsBuildTask(userId, input);
        return {
          worker,
          status: 'dispatched' as const,
          agentUrl: result.conversationUrl,
          conversationId: result.conversationId,
          openHandsApiVersion: result.apiVersion,
          openHands: result,
        };
      } catch (err) {
        return {
          worker,
          status: 'error' as const,
          error: err instanceof Error ? err.message : 'Builder dispatch failed',
        };
      }
    }

    if (worker === 'FOUNDER_NODE') {
      return {
        worker,
        status: 'queued' as const,
        message:
          'Founder Node is connected for chat — use Execute after queuing a spec, or dispatch via Quick Build on desktop.',
      };
    }

    return {
      worker: 'NONE' as const,
      status: 'queued' as const,
      message: 'Task queued. Connect a builder worker in Settings to auto-dispatch remotely.',
    };
  }

  async getWorkerStatus(userId: string) {
    const connections = await this.getBuildWorkerConnections(userId);
    const worker = resolveBuildWorker(connections);
    const buildWorkerOptions: { key: 'CURSOR' | 'OPENHANDS'; label: string }[] = [];
    if (connections.cursor) buildWorkerOptions.push({ key: 'CURSOR', label: 'Cursor' });
    if (connections.openHands) buildWorkerOptions.push({ key: 'OPENHANDS', label: 'OpenHands' });
    const llmProviders = await this.listUsableLlmCredentialProviders(userId);
    const githubTokenConnected = Boolean(
      await this.prisma.gitHubConnection.findFirst({
        where: { userId, accessTokenEncrypted: { not: null } },
      }),
    );
    const cursorMeta = await this.getCursorMeta(userId);
    return {
      buildWorker: worker,
      buildWorkerOptions,
      connections,
      llmConnected: llmProviders.size > 0,
      githubConnected: githubTokenConnected,
      cursorAgentUrl: cursorMeta?.agentId ? `https://cursor.com/agents/${cursorMeta.agentId}` : null,
      latestRunId: cursorMeta?.latestRunId ?? null,
      cursorAgentId: cursorMeta?.agentId ?? null,
    };
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
    const result = await this.tryCopilotChatCompletion(userId, system, userPrompt);
    return result.ok ? result.text : null;
  }

  /**
   * Chat completion for Founder Copilot — uses default LLM when set, otherwise any
   * connected API key (DeepSeek, OpenAI, etc.) even if default is Cursor/OpenHands.
   */
  async tryCopilotChatCompletion(
    userId: string,
    system: string,
    userPrompt: string,
    options?: { forceProvider?: AiProvider },
  ): Promise<
    | { ok: true; text: string; provider: AiProvider }
    | { ok: false; llmErrors: string[] }
  > {
    if (options?.forceProvider) {
      return this.tryCopilotChatCompletionForced(userId, system, userPrompt, options.forceProvider);
    }

    const settings = await this.ensureSettings(userId);
    const usable = await this.listUsableLlmCredentialProviders(userId);
    const order: AiProvider[] = [];
    const llmErrors: string[] = [];

    if (settings.defaultProvider === AiProvider.OLLAMA_LOCAL) {
      const nodeResult = await this.founderNodeInference.runViaFounderNode(
        userId,
        system,
        userPrompt,
        settings.preferredModel,
      );
      if (nodeResult.ok) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.OLLAMA_LOCAL,
          system,
          userPrompt,
          nodeResult.text,
          'copilot',
        );
        return { ok: true, text: nodeResult.text, provider: AiProvider.OLLAMA_LOCAL };
      }
      llmErrors.push(...nodeResult.errors);

      const direct = await this.tryDirectOllama(userId, system, userPrompt, settings.preferredModel);
      if (direct.ok) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.OLLAMA_LOCAL,
          system,
          userPrompt,
          direct.text,
          'copilot',
        );
        return { ok: true, text: direct.text, provider: AiProvider.OLLAMA_LOCAL };
      }
      if (direct.error) llmErrors.push(direct.error);
    }

    if (settings.defaultProvider === AiProvider.PHALA) {
      const phala = await this.resolvePhalaCredentials(userId);
      if (phala) {
        try {
          const chat = await callPhalaChat({
            apiKey: phala.apiKey,
            inferenceUrl: phala.inferenceUrl,
            model: settings.preferredModel ?? phala.model,
            system,
            userPrompt,
          });
          if (chat?.text) {
            await this.recordPhalaChat(userId, chat);
            await this.logAiTokenUsage(
              userId,
              AiProvider.PHALA,
              system,
              userPrompt,
              chat.text,
              'copilot',
            );
            return { ok: true, text: chat.text, provider: AiProvider.PHALA };
          }
          llmErrors.push('PHALA: empty response');
        } catch (err) {
          llmErrors.push(`PHALA: ${err instanceof Error ? err.message : 'request failed'}`);
        }
      } else {
        llmErrors.push('PHALA: connect Phala Private AI or enable platform credits');
      }
    }

    if (
      settings.defaultProvider !== AiProvider.RULE_BASED &&
      !isRemoteAgentProvider(settings.defaultProvider) &&
      !isFounderNodeAiProvider(settings.defaultProvider)
    ) {
      order.push(settings.defaultProvider);
    }

    for (const key of [
      AiProvider.PHALA,
      AiProvider.OPENROUTER,
      AiProvider.DEEPSEEK,
      AiProvider.OPENAI,
      AiProvider.ANTHROPIC,
      AiProvider.GEMINI,
    ]) {
      if (!order.includes(key)) order.push(key);
    }

    for (const provider of order) {
      const cfg = aiProviderConfig(provider);
      if (!cfg?.credentialProvider) continue;
      if (provider === AiProvider.PHALA) {
        const phala = await this.resolvePhalaCredentials(userId);
        if (!phala) continue;
        const model =
          provider === settings.defaultProvider
            ? settings.preferredModel ?? phala.model
            : phala.model;
        try {
          const chat = await callPhalaChat({
            apiKey: phala.apiKey,
            inferenceUrl: phala.inferenceUrl,
            model,
            system,
            userPrompt,
          });
          if (chat?.text) {
            await this.recordPhalaChat(userId, chat);
            await this.logAiTokenUsage(userId, provider, system, userPrompt, chat.text, 'copilot');
            return { ok: true, text: chat.text, provider };
          }
          llmErrors.push(`${provider}: empty response`);
        } catch (err) {
          llmErrors.push(`${provider}: ${err instanceof Error ? err.message : 'request failed'}`);
        }
        continue;
      }
      if (cfg.connectMode !== 'api_key') continue;
      if (!usable.has(cfg.credentialProvider)) continue;

      const cred = await this.prisma.integrationCredential.findUnique({
        where: { userId_provider: { userId, provider: cfg.credentialProvider } },
      });
      const apiKey = this.crypto.decrypt(cred?.token);
      if (!apiKey) continue;

      const model =
        provider === settings.defaultProvider
          ? settings.preferredModel ?? cfg.defaultModel ?? undefined
          : cfg.defaultModel ?? undefined;

      try {
        const result = await this.completionWithProvider(provider, apiKey, system, userPrompt, model);
        if (result?.text?.trim()) {
          await this.logAiTokenUsage(
            userId,
            provider,
            system,
            userPrompt,
            result.text.trim(),
            'copilot',
            result.usage,
          );
          return { ok: true, text: result.text.trim(), provider };
        }
        llmErrors.push(`${provider}: empty response`);
      } catch (err) {
        llmErrors.push(
          `${provider}: ${err instanceof Error ? err.message : 'request failed'}`,
        );
      }
    }

    return llmErrors.length > 0 ? { ok: false, llmErrors } : { ok: false, llmErrors: ['No LLM API key configured'] };
  }

  /** Single-provider completion (Social Hub draft buttons). */
  private async tryCopilotChatCompletionForced(
    userId: string,
    system: string,
    userPrompt: string,
    forceProvider: AiProvider,
  ): Promise<
    | { ok: true; text: string; provider: AiProvider }
    | { ok: false; llmErrors: string[] }
  > {
    const settings = await this.ensureSettings(userId);
    const llmErrors: string[] = [];

    if (forceProvider === AiProvider.OLLAMA_LOCAL) {
      const nodeResult = await this.founderNodeInference.runViaFounderNode(
        userId,
        system,
        userPrompt,
        settings.preferredModel,
      );
      if (nodeResult.ok) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.OLLAMA_LOCAL,
          system,
          userPrompt,
          nodeResult.text,
          'copilot_forced',
        );
        return { ok: true, text: nodeResult.text, provider: AiProvider.OLLAMA_LOCAL };
      }
      llmErrors.push(...nodeResult.errors);
      const direct = await this.tryDirectOllama(userId, system, userPrompt, settings.preferredModel);
      if (direct.ok) {
        await this.logAiTokenUsage(
          userId,
          AiProvider.OLLAMA_LOCAL,
          system,
          userPrompt,
          direct.text,
          'copilot_forced',
        );
        return { ok: true, text: direct.text, provider: AiProvider.OLLAMA_LOCAL };
      }
      if (direct.error) llmErrors.push(direct.error);
      return { ok: false, llmErrors };
    }

    if (forceProvider === AiProvider.PHALA) {
      const phala = await this.resolvePhalaCredentials(userId);
      if (!phala) return { ok: false, llmErrors: ['PHALA: not connected'] };
      try {
        const chat = await callPhalaChat({
          apiKey: phala.apiKey,
          inferenceUrl: phala.inferenceUrl,
          model: settings.preferredModel ?? phala.model,
          system,
          userPrompt,
        });
        if (chat?.text) {
          await this.recordPhalaChat(userId, chat);
          await this.logAiTokenUsage(
            userId,
            AiProvider.PHALA,
            system,
            userPrompt,
            chat.text,
            'copilot_forced',
          );
          return { ok: true, text: chat.text, provider: AiProvider.PHALA };
        }
        return { ok: false, llmErrors: ['PHALA: empty response'] };
      } catch (err) {
        return {
          ok: false,
          llmErrors: [`PHALA: ${err instanceof Error ? err.message : 'request failed'}`],
        };
      }
    }

    const cfg = aiProviderConfig(forceProvider);
    if (!cfg?.credentialProvider || cfg.connectMode !== 'api_key') {
      return { ok: false, llmErrors: [`${forceProvider}: not available as chat provider`] };
    }

    const usable = await this.listUsableLlmCredentialProviders(userId);
    if (!usable.has(cfg.credentialProvider)) {
      return { ok: false, llmErrors: [`${forceProvider}: connect API key in AI Stack`] };
    }

    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: cfg.credentialProvider } },
    });
    const apiKey = this.crypto.decrypt(cred?.token);
    if (!apiKey) return { ok: false, llmErrors: [`${forceProvider}: invalid credential`] };

    const model =
      settings.defaultProvider === forceProvider
        ? settings.preferredModel ?? cfg.defaultModel ?? undefined
        : cfg.defaultModel ?? undefined;

    try {
      const result = await this.completionWithProvider(forceProvider, apiKey, system, userPrompt, model);
      if (result?.text?.trim()) {
        await this.logAiTokenUsage(
          userId,
          forceProvider,
          system,
          userPrompt,
          result.text.trim(),
          'copilot_forced',
          result.usage,
        );
        return { ok: true, text: result.text.trim(), provider: forceProvider };
      }
      return { ok: false, llmErrors: [`${forceProvider}: empty response`] };
    } catch (err) {
      return {
        ok: false,
        llmErrors: [`${forceProvider}: ${err instanceof Error ? err.message : 'request failed'}`],
      };
    }
  }

  private async completionWithProvider(
    provider: AiProvider,
    apiKey: string,
    system: string,
    userPrompt: string,
    model?: string,
  ): Promise<{ text: string; usage: LlmUsage | null } | null> {
    switch (provider) {
      case AiProvider.OPENAI:
        return this.callOpenAi(apiKey, system, userPrompt, model);
      case AiProvider.ANTHROPIC:
        return this.callAnthropic(apiKey, system, userPrompt, model);
      case AiProvider.GEMINI:
        return this.callGemini(apiKey, system, userPrompt, model);
      case AiProvider.DEEPSEEK:
        return this.callDeepSeek(apiKey, system, userPrompt, model);
      case AiProvider.OPENROUTER:
        return this.callOpenRouter(apiKey, system, userPrompt, model);
      default:
        return null;
    }
  }

  private async logAiTokenUsage(
    userId: string,
    provider: AiProvider | string,
    system: string,
    userPrompt: string,
    text: string,
    source: string,
    usage?: LlmUsage | null,
  ) {
    const promptTokens =
      usage?.promptTokens ?? estimateLlmTokensFromText(`${system}\n${userPrompt}`);
    const completionTokens = usage?.completionTokens ?? estimateLlmTokensFromText(text);
    const projectId = await this.resolvePrimaryProjectId(userId);
    await this.adoption
      .recordAiUsage({
        userId,
        provider: String(provider),
        source,
        promptTokens,
        completionTokens,
        projectId,
      })
      .catch(() => undefined);
  }

  private async resolvePrimaryProjectId(userId: string): Promise<string | null> {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      select: {
        projects: { where: { approved: true }, take: 1, select: { id: true }, orderBy: { updatedAt: 'desc' } },
      },
    });
    return founder?.projects[0]?.id ?? null;
  }

  private async tryDirectOllama(
    userId: string,
    system: string,
    userPrompt: string,
    preferredModel?: string | null,
  ): Promise<{ ok: true; text: string } | { ok: false; error?: string }> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'ollama' } },
    });
    const meta = cred?.metadata as { baseUrl?: string; model?: string } | null;
    const baseUrl = meta?.baseUrl?.trim();
    if (!baseUrl) return { ok: false };

    try {
      const text = await this.callOllama(
        baseUrl,
        system,
        userPrompt,
        preferredModel?.trim() || meta?.model || 'llama3.2',
      );
      if (text?.trim()) return { ok: true, text: text.trim() };
      return { ok: false, error: 'Ollama returned empty response' };
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : 'Direct Ollama request failed',
      };
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

  private async getPhalaMeta(userId: string): Promise<PhalaCredentialMeta | null> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'phala' } },
    });
    return (cred?.metadata as PhalaCredentialMeta | null) ?? null;
  }

  private platformPhalaAvailable(): boolean {
    return Boolean(process.env.PHALA_API_KEY?.trim());
  }

  private async getPhalaPrivateAiStatus(userId: string) {
    const meta = await this.getPhalaMeta(userId);
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'phala' } },
      select: { token: true },
    });
    const userKey = Boolean(cred?.token && this.crypto.decrypt(cred.token));
    const platformAvailable = this.platformPhalaAvailable();
    return {
      ready: userKey || platformAvailable,
      userKeyConnected: userKey,
      platformAvailable,
      inferenceUrl:
        meta?.inferenceUrl ||
        normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL || DEFAULT_PHALA_INFERENCE_URL),
      model: meta?.model || process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
      docsUrl: 'https://docs.phala.com/phala-cloud/confidential-ai/confidential-model/confidential-ai-api',
    };
  }

  private async recordPhalaChat(userId: string, chat: PhalaChatResult) {
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    await this.attestation
      .recordPhalaInference(userId, chat, settings?.memoryStorageMode)
      .catch(() => undefined);
  }

  private async resolvePhalaCredentials(userId: string) {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'phala' } },
    });
    const userKey = this.crypto.decrypt(cred?.token);
    const meta = (cred?.metadata as PhalaCredentialMeta | null) ?? null;
    if (userKey) {
      return {
        apiKey: userKey,
        inferenceUrl: meta?.inferenceUrl || normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL),
        model: meta?.model || process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
        source: 'user' as const,
      };
    }
    const platformKey = process.env.PHALA_API_KEY?.trim();
    if (!platformKey) return null;
    return {
      apiKey: platformKey,
      inferenceUrl: normalizePhalaBaseUrl(process.env.PHALA_INFERENCE_URL || DEFAULT_PHALA_INFERENCE_URL),
      model: process.env.PHALA_MODEL?.trim() || DEFAULT_PHALA_MODEL,
      source: 'platform' as const,
    };
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

  /** LLM keys with a stored token (verified optional — chat should still attempt). */
  private async listUsableLlmCredentialProviders(userId: string) {
    const creds = await this.prisma.integrationCredential.findMany({
      where: {
        userId,
        provider: { in: ['openai', 'anthropic', 'gemini', 'deepseek', 'openrouter', 'phala'] },
      },
      select: { provider: true, token: true },
    });
    const out = new Set<string>();
    for (const c of creds) {
      if (this.crypto.decrypt(c.token)) out.add(c.provider);
    }
    return out;
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
      case 'deepseek': {
        const res = await fetch('https://api.deepseek.com/models', {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (!res.ok) {
          const ping = await fetch('https://api.deepseek.com/chat/completions', {
            method: 'POST',
            headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({
              model: 'deepseek-chat',
              messages: [{ role: 'user', content: 'ping' }],
              max_tokens: 8,
            }),
          });
          if (!ping.ok) throw new BadRequestException('Invalid DeepSeek API key');
        }
        return { accountName: 'DeepSeek account' };
      }
      case 'openrouter': {
        const res = await fetch('https://openrouter.ai/api/v1/models', {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (!res.ok) throw new BadRequestException('Invalid OpenRouter API key');
        return { accountName: 'OpenRouter account' };
      }
      case 'phala': {
        const verified = await verifyPhalaConnection({ apiKey: key });
        if (!verified.ok) throw new BadRequestException(verified.reason);
        return { accountName: 'Phala Private AI' };
      }
      default:
        throw new BadRequestException(`Unknown AI provider: ${provider}`);
    }
  }

  private async verifyOllamaUrl(baseUrl: string): Promise<void> {
    const res = await fetch(`${baseUrl}/api/tags`, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new BadRequestException('Cannot reach Ollama at that URL — is it running?');
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
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const text = data.choices?.[0]?.message?.content;
    if (!text) return null;
    const usage = parseOpenAiStyleUsage(data);
    return { text, usage };
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
    const data = (await res.json()) as {
      content?: { type: string; text?: string }[];
      usage?: { input_tokens?: number; output_tokens?: number };
    };
    const text = data.content?.find((c) => c.type === 'text')?.text;
    if (!text) return null;
    return { text, usage: parseAnthropicUsage(data) };
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
      usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number };
    };
    const text = data.candidates?.[0]?.content?.parts?.[0]?.text;
    if (!text) return null;
    const usage =
      data.usageMetadata != null
        ? {
            promptTokens: data.usageMetadata.promptTokenCount ?? 0,
            completionTokens: data.usageMetadata.candidatesTokenCount ?? 0,
          }
        : null;
    return { text, usage };
  }

  private async callDeepSeek(key: string, system: string, user: string, model?: string) {
    const res = await fetch('https://api.deepseek.com/chat/completions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: model ?? 'deepseek-chat',
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
        temperature: 0.4,
      }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const text = data.choices?.[0]?.message?.content;
    if (!text) return null;
    return { text, usage: parseOpenAiStyleUsage(data) };
  }

  private async callOpenRouter(key: string, system: string, user: string, model?: string) {
    const res = await fetch('https://openrouter.ai/api/v1/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://doxxedcrypto.digital',
        'X-Title': 'Doxxed Founder OS',
      },
      body: JSON.stringify({
        model: model ?? 'openrouter/auto',
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
        temperature: 0.4,
      }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const text = data.choices?.[0]?.message?.content;
    if (!text) return null;
    return { text, usage: parseOpenAiStyleUsage(data) };
  }

  private async callOllama(baseUrl: string, system: string, user: string, model: string) {
    const res = await fetch(`${baseUrl.replace(/\/+$/, '')}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        stream: false,
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: user },
        ],
      }),
      signal: AbortSignal.timeout(120_000),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`Ollama HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
    }
    const data = (await res.json()) as { message?: { content?: string } };
    return data.message?.content ?? null;
  }
}
